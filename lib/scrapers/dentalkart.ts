import { smartFetch } from "../http";
import { ProductData, ProductVariant } from "../types";
import { detectPackSize, calculateUnitPrice } from "../pack-detector";
import { parsePdpHtml } from "../pdp";
import { parseVariantSpec, hasSizeSignal } from "../variant-spec";
import * as cheerio from "cheerio";

/** Significant lowercase tokens of a product name, minus config/stopwords —
 *  used to decide which simple products on a grouped PDP are real children. */
function coreTokens(name: string): Set<string> {
  const STOP = new Set([
    "with", "and", "the", "of", "for", "set", "only", "basic", "plus", "premium",
    "standard", "deluxe", "non", "torque", "ratchet", "box", "kit",
  ]);
  return new Set(
    name
      .toLowerCase()
      .replace(/[()]/g, " ")
      .split(/[^a-z0-9-]+/)
      .filter((w) => w.length > 1 && !STOP.has(w)),
  );
}

const DK_CDN = "https://r2dkmedia.dentalkart.com";
const DK_MEDIA_PREFIX = "/media/catalog/product";

/**
 * Build a working Dentalkart image URL from any form the site emits (relative
 * path, protocol-relative, legacy images1 host, or a bare r2dkmedia URL). DK
 * media lives under /media/catalog/product; a path that omits it (e.g.
 * "/ctlp/i/m/img.jpg" or "https://r2dkmedia…/ctlp/…") returns 404 — the JSON-LD
 * and RSC child media give exactly those bare paths, which is why many images
 * rendered as broken/skeleton.
 */
function dkImageUrl(raw: string): string {
  if (!raw) return "";
  let r = raw.trim();
  if (r.startsWith("//")) r = `https:${r}`;
  if (/^https?:\/\//i.test(r)) {
    r = r.replace(/^https?:\/\/images1\.dentalkart\.com/i, DK_CDN);
    const m = r.match(/^https?:\/\/r2dkmedia\.dentalkart\.com\/(.*)$/i);
    if (m && !/^media\/catalog\/product\//i.test(m[1].replace(/^\/+/, ""))) {
      return `${DK_CDN}${DK_MEDIA_PREFIX}/${m[1].replace(/^\/+/, "")}`;
    }
    return r;
  }
  const rel = r.replace(/^\/+/, "");
  return rel.startsWith("media/catalog/product")
    ? `${DK_CDN}/${rel}`
    : `${DK_CDN}${DK_MEDIA_PREFIX}/${rel}`;
}

/**
 * Parse the sub-variants (children) of a Dentalkart GROUPED product out of the
 * Next.js RSC flight payload embedded in the PDP HTML.
 *
 * Each child is a "simple" product object carrying name / sku / is_in_stock and
 * a `pricing` reference ("$83") that points at a pricing row with `price` (MRP)
 * and `selling_price`. The grouped parent's headline price (from the search
 * API) is often just the cheapest child, so without this every variant collapses
 * to one wrong price.
 */
function parseGroupedChildren(html: string, mainName: string): ProductVariant[] {
  const chunks = [...html.matchAll(/self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)/g)];
  if (!chunks.length) return [];
  let flight = "";
  for (const c of chunks) {
    try {
      flight += JSON.parse(`"${c[1]}"`);
    } catch {
      // skip an unparseable chunk
    }
  }
  if (!flight) return [];

  // The flight is a list of "id:value" rows; product and pricing rows are flat
  // (values are $refs or primitives — no nested braces). Build an id → text map
  // so fields can be read order-independently. DK varies the field set across
  // products (extra action_btn / rating / has_spare_parts …), which broke the
  // old fixed-order regex.
  const rows = new Map<string, string>();
  const rowRe = /(?:^|\n)([0-9a-f]+):/g;
  const marks: Array<{ id: string; cs: number; ls: number }> = [];
  let mm: RegExpExecArray | null;
  while ((mm = rowRe.exec(flight))) marks.push({ id: mm[1], cs: mm.index + mm[0].length, ls: mm.index });
  for (let i = 0; i < marks.length; i++) {
    const end = i + 1 < marks.length ? marks[i + 1].ls : flight.length;
    rows.set(marks[i].id, flight.slice(marks[i].cs, end));
  }

  const unescape = (s: string): string => {
    try {
      return JSON.parse(`"${s}"`);
    } catch {
      return s;
    }
  };
  const nameOf = (row: string): string =>
    unescape(row.match(/"name":"((?:[^"\\]|\\.)*)"/)?.[1] ?? "");

  // Per-child image: row has "media":"$ref" → array ["$inner"] → media object
  // with a "file" path (e.g. "ctlp/s/i/size17.jpg"). Each child has its own.
  const imageOf = (row: string): string => {
    const mref = row.match(/"media":"\$([0-9a-f]+)"/)?.[1];
    if (!mref) return "";
    const arr = rows.get(mref) ?? "";
    const inner = arr.match(/\$([0-9a-f]+)/)?.[1];
    const mediaObj = inner ? rows.get(inner) ?? arr : arr;
    const file = mediaObj.match(/"file":"((?:[^"\\]|\\.)*)"/)?.[1];
    return file ? dkImageUrl(unescape(file)) : "";
  };

  const buildFromRow = (row: string): ProductVariant | null => {
    const name = nameOf(row);
    const priceRef = row.match(/"pricing":"\$([0-9a-f]+)"/)?.[1];
    if (!name || !priceRef) return null;
    const priceRow = rows.get(priceRef) ?? "";
    const selling = Number(priceRow.match(/"selling_price":(\d+(?:\.\d+)?)/)?.[1] ?? 0);
    const mrp = Number(priceRow.match(/"price":(\d+(?:\.\d+)?)/)?.[1] ?? 0);
    const price = selling || mrp;
    if (price <= 0) return null;
    const inStock = (row.match(/"is_in_stock":(true|false)/)?.[1] ?? "true") === "true";
    const packSize = detectPackSize(name, "", "");
    return {
      name,
      sku: row.match(/"sku":"([^"]+)"/)?.[1] ?? "",
      price,
      mrp: mrp || price,
      packSize,
      unitPrice: calculateUnitPrice(price, packSize),
      variantSpec: parseVariantSpec(name),
      inStock, // carry stock so the matcher can show out-of-stock variants
      image: imageOf(row),
    };
  };

  // Authoritative child list: a grouped product carries
  // "child_products":"$REF" → an array row ["$id1","$id2",…] of child rows.
  // Prefer the object whose name matches the page's main product; else the
  // first non-empty list.
  let childIds: string[] = [];
  const otherLists: string[][] = [];
  for (const [, row] of rows) {
    const cpRef = row.match(/"child_products":"\$([0-9a-f]+)"/)?.[1];
    if (!cpRef) continue;
    const ids = [...(rows.get(cpRef) ?? "").matchAll(/"\$([0-9a-f]+)"/g)].map((x) => x[1]);
    if (!ids.length) continue;
    if (nameOf(row).toLowerCase() === mainName.toLowerCase()) {
      childIds = ids;
      break;
    }
    otherLists.push(ids);
  }
  if (!childIds.length && otherLists.length) childIds = otherLists[0];

  const out: ProductVariant[] = [];
  const seen = new Set<string>();
  const push = (v: ProductVariant | null) => {
    const key = v && (v.sku || v.name);
    if (v && key && !seen.has(key)) {
      seen.add(key);
      out.push(v);
    }
  };

  if (childIds.length) {
    for (const id of childIds) {
      const row = rows.get(id);
      if (row) push(buildFromRow(row));
    }
    return out;
  }

  // Fallback (older RSC shape without a resolvable child list): scan product
  // rows, keep real siblings (brand + ≥1 shared core token with the parent),
  // and drop the parent itself + unrelated recommended products.
  const brand = mainName.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean)[0] || "";
  const mainCore = new Set(
    [...coreTokens(mainName)].filter((t) => /^[a-z]{3,}$/.test(t) && t !== brand),
  );
  for (const [, row] of rows) {
    if (!row.includes('"sku":"') || !row.includes('"pricing":"$')) continue;
    const v = buildFromRow(row);
    if (!v || v.name.toLowerCase() === mainName.toLowerCase()) continue;
    const toks = coreTokens(v.name);
    const hasBrand = brand ? toks.has(brand) : true;
    let shared = 0;
    for (const t of mainCore) if (toks.has(t)) shared++;
    if (hasBrand && (mainCore.size === 0 || shared >= 1)) push(v);
  }
  return out;
}

const SEARCH_API_URL =
  "https://apis.dentalkart.com/search/api/v1/query/results";

const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

/**
 * Scrapes Dentalkart search results via their internal search API.
 *
 * Dentalkart uses a Next.js frontend with fully client-side rendered search.
 * Product data is fetched from:
 *   https://apis.dentalkart.com/search/api/v1/query/results?query={term}&platform=web
 *
 * The API returns an Algolia-style response with:
 *   data.hits.hits[] - array of product objects
 *
 * Each product contains:
 *   - name: product name
 *   - url: full product URL (e.g. https://www.dentalkart.com/p/{slug}.html)
 *   - image_url / thumbnail_url: image path (needs https: prefix)
 *   - price.INR.default: current selling price
 *   - price.INR.default_original_formated: MRP (original price)
 *   - prices.regularPrice.amount.value: MRP
 *   - prices.minimalPrice.amount.value: selling price
 *   - discount_percentage: discount %
 *   - in_stock: 1 = in stock, 0 = out of stock
 *   - short_description: brief product description
 *   - manufacturer: brand name
 */
export async function searchDentalkart(
  productName: string
): Promise<ProductData[]> {
  try {
    const url = `${SEARCH_API_URL}?query=${encodeURIComponent(productName)}&platform=web`;
    const response = await smartFetch(url, { accept: "application/json", skipReferer: true });

    if (!response.ok) return [];

    const data = await response.json();
    const hits: DentalkartProduct[] = data?.hits?.hits || [];

    if (!Array.isArray(hits) || hits.length === 0) return [];

    return hits.slice(0, 5).map(mapProduct);
  } catch {
    return [];
  }
}

interface DentalkartPrice {
  INR?: {
    default?: number;
    default_original_formated?: number;
  };
}

interface DentalkartPriceAmount {
  amount?: {
    value?: number;
  };
}

interface DentalkartPrices {
  regularPrice?: DentalkartPriceAmount;
  minimalPrice?: DentalkartPriceAmount;
}

interface DentalkartProduct {
  name?: string;
  url?: string;
  url_key?: string;
  image_url?: string;
  thumbnail_url?: string;
  price?: DentalkartPrice;
  prices?: DentalkartPrices;
  discount_percentage?: number;
  in_stock?: number;
  short_description?: string;
  manufacturer?: string;
  sku?: string;
  // DK's API has been observed to return this as BOTH a string (older SKUs)
  // and an array of strings (newer SKUs, often empty `[]`). Handle both.
  packaging_contents?: string | string[];
  categories?: string[];
  // Grouped products list their sub-variants here, e.g.
  //   "... - Big Pack ( 15g Powder + 13.1g Liquid), ... (Extra) ... Mini Pack (5g Powder + 3g Liquid)"
  child_names?: string;
  full_description?: string;
}

/**
 * Pick the source-of-truth size/composition spec for a Dentalkart listing.
 *
 * Grouped products bundle several sub-variants; `child_names` lists them in
 * display order. We take the FIRST non-"Extra" child as the primary spec
 * (matches DK's headline listing), e.g. GC Gold Label 9 → "15g Powder +
 * 13.1g Liquid". Falls back to the product's own name/packaging text for
 * simple (non-grouped) products, where it usually yields no size signal and the
 * matcher just ignores it.
 */
function dentalkartTruthSpec(
  childNames: string,
  packaging: string,
  name: string,
  description: string,
) {
  if (childNames && childNames.trim()) {
    // Children are comma-separated; specs use " + " internally (no commas), so
    // split on ")," boundaries and re-attach the closing paren.
    const parts = childNames.split(/\)\s*,\s*/).map((s) => s.trim());
    const children = parts.map((s, i) => (i < parts.length - 1 ? `${s})` : s));
    const primary = children.find((c) => !/\bextra\b/i.test(c)) || children[0];
    if (primary) {
      const spec = parseVariantSpec(primary);
      if (hasSizeSignal(spec)) return spec;
    }
  }
  // Simple product: try name + packaging + description.
  return parseVariantSpec(`${name} ${packaging} ${description}`);
}

function mapProduct(p: DentalkartProduct): ProductData {
  const name = (p.name || "").trim();

  // Product URL
  const productUrl = p.url || (p.url_key ? `https://www.dentalkart.com/${p.url_key}` : "");

  // Image URL — API returns relative media paths, protocol-relative or absolute
  // (incl. bare r2dkmedia paths that 404). dkImageUrl normalizes every case.
  const image = dkImageUrl(p.image_url || p.thumbnail_url || "");

  // Prices
  const price =
    p.price?.INR?.default ||
    p.prices?.minimalPrice?.amount?.value ||
    0;

  const mrp =
    p.price?.INR?.default_original_formated ||
    p.prices?.regularPrice?.amount?.value ||
    price;

  const discount = p.discount_percentage
    ? Math.round(p.discount_percentage)
    : mrp > 0 && price > 0 && mrp !== price
      ? Math.round(((mrp - price) / mrp) * 100)
      : 0;

  const inStock = p.in_stock === 1;
  const packSize = detectPackSize(name, p.short_description, productUrl);
  const unitPrice = calculateUnitPrice(price, packSize);

  // Build packaging info: prefer packaging_contents, fall back to manufacturer.
  // Guard against the array form — `[] || "x"` returns `[]` (truthy), which
  // used to swallow the fallback and leave every product's packaging blank.
  const rawPackaging = Array.isArray(p.packaging_contents)
    ? p.packaging_contents.filter(Boolean).join(", ")
    : p.packaging_contents;
  const packaging = rawPackaging || p.manufacturer || "";

  const variantSpec = dentalkartTruthSpec(
    p.child_names || "",
    typeof packaging === "string" ? packaging : "",
    name,
    p.full_description || p.short_description || "",
  );

  return {
    name,
    url: productUrl,
    image,
    price,
    mrp,
    discount,
    packaging,
    inStock,
    description: p.short_description || "",
    source: "dentalkart",
    packSize,
    unitPrice,
    sku: p.sku || undefined,
    variantSpec,
  };
}

/**
 * Fetch a single Dentalkart PDP (Next.js, but product pages are
 * server-rendered for SEO and carry a schema.org Product JSON-LD block).
 * Returns null when the page can't be parsed — caller falls back to the
 * (thinner) search-API data for that product.
 */
export async function fetchDentalkartProduct(url: string): Promise<ProductData | null> {
  try {
    const response = await smartFetch(url, { timeout: 15000 });
    if (!response.ok) return null;
    const html = await response.text();
    // DK serves a soft-404 ("Product Not Found", HTTP 200) for delisted/broken
    // products and redirects to the child slug. Its "related products" carousel
    // would otherwise be mis-parsed as children — bail so we never surface junk.
    if (/<title>\s*Product Not Found\s*<\/title>/i.test(html)) return null;
    const $ = cheerio.load(html);
    let pdp = parsePdpHtml(html);
    if (!pdp) {
      // Some grouped products ship NO JSON-LD Product node (e.g. the Julldent
      // "JULL-DENT 073" needle holder) — recover the name from the page head and
      // lean on the RSC children for pricing/variants so the matcher can still
      // resolve to the exact sub-variant instead of failing the whole PDP.
      const headName = (
        $('meta[property="og:title"]').attr("content") ||
        $("h1").first().text() ||
        $("title").text() ||
        ""
      )
        .replace(/\s+/g, " ")
        .replace(/\s*[|–-]\s*Dentalkart.*$/i, "")
        .trim();
      const kids = parseGroupedChildren(html, headName);
      if (!headName || !kids.length) return null;
      const prices = kids.map((v) => v.price).filter((p) => p > 0);
      pdp = {
        name: headName,
        description: "",
        image: kids.find((v) => v.image)?.image || "",
        price: prices.length ? Math.min(...prices) : 0,
        mrp: 0,
        brand: "",
        inStock: kids.some((v) => v.inStock),
        sku: "",
      };
    }

    // Long description / key-features live in the description tab; the
    // JSON-LD description is often the short one. Concatenate both.
    const longDesc = $(
      '[class*="description"], [id*="description"], [class*="product-detail"]'
    )
      .first()
      .text()
      .replace(/\s+/g, " ")
      .trim();
    const description = [pdp.description, longDesc]
      .filter(Boolean)
      .filter((d, i, a) => a.indexOf(d) === i)
      .join(". ")
      .slice(0, 4000);

    const packSize = detectPackSize(pdp.name, description, url);
    // Grouped products: pull the real per-child names/prices/stock from the RSC
    // payload so the matcher can resolve to the exact sub-variant.
    const variants = parseGroupedChildren(html, pdp.name);
    return {
      name: pdp.name,
      url,
      image: dkImageUrl(pdp.image),
      price: pdp.price,
      mrp: pdp.mrp,
      discount:
        pdp.mrp > pdp.price && pdp.mrp > 0
          ? Math.round(((pdp.mrp - pdp.price) / pdp.mrp) * 100)
          : 0,
      packaging: pdp.brand || "",
      inStock: pdp.inStock ?? true,
      description,
      source: "dentalkart",
      packSize,
      unitPrice: calculateUnitPrice(pdp.price, packSize),
      sku: pdp.sku || undefined,
      variants: variants.length ? variants : undefined,
      variantSpec: parseVariantSpec(`${pdp.name} ${description}`),
    };
  } catch {
    return null;
  }
}
