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

  // A child is a real sibling if it shares the brand (first token) AND at least
  // one descriptive core token with the parent. Children often carry their OWN
  // code instead of the parent's (e.g. "(KGF 8)" not "(JULL-DENT 191)") and may
  // differ in plural ("Knives" vs "Knife"), so a strict overlap ratio wrongly
  // drops them — while still excluding unrelated recommended products.
  const brand = mainName.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean)[0] || "";
  const mainCore = new Set(
    [...coreTokens(mainName)].filter((t) => /^[a-z]{3,}$/.test(t) && t !== brand),
  );
  const out: ProductVariant[] = [];
  const seen = new Set<string>();
  const childRe =
    /"is_in_stock":(true|false)[^{}]*?"name":"((?:[^"\\]|\\.)*)","pricing":"\$([0-9a-f]+)","product_id":\d+,"seo":"\$[0-9a-f]+","sku":"([^"]+)"/g;
  let m: RegExpExecArray | null;
  while ((m = childRe.exec(flight))) {
    const inStock = m[1] === "true";
    let name = "";
    try {
      name = JSON.parse(`"${m[2]}"`);
    } catch {
      name = m[2];
    }
    const priceRef = m[3];
    const sku = m[4];
    if (seen.has(sku)) continue;

    // Only keep real siblings of this grouped product (drop related/recommended
    // simple products that also live in the payload).
    const toks = coreTokens(name);
    const hasBrand = brand ? toks.has(brand) : true;
    let shared = 0;
    for (const t of mainCore) if (toks.has(t)) shared++;
    if (!(hasBrand && (mainCore.size === 0 || shared >= 1))) continue;

    const rowRe = new RegExp(`\\n${priceRef}:\\{[^}]*\\}`);
    const row = flight.match(rowRe)?.[0] ?? "";
    const selling = Number(row.match(/"selling_price":(\d+(?:\.\d+)?)/)?.[1] ?? 0);
    const mrp = Number(row.match(/"price":(\d+(?:\.\d+)?)/)?.[1] ?? 0);
    const price = selling || mrp;
    if (price <= 0) continue;

    seen.add(sku);
    const packSize = detectPackSize(name, "", "");
    out.push({
      name,
      sku,
      price,
      mrp: mrp || price,
      packSize,
      unitPrice: calculateUnitPrice(price, packSize),
      variantSpec: parseVariantSpec(name),
      inStock, // carry stock so the matcher can show out-of-stock variants
    });
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

  // Image URL — the API can return:
  //   1. Protocol-relative: //images1.dentalkart.com/... (legacy)
  //   2. Absolute: https://images1.dentalkart.com/... or https://r2dkmedia... (already fine)
  //   3. Relative media path: /s/5/s5083-1.jpg or /u/n/untitled.jpg (most common now)
  // Live CDN is r2dkmedia.dentalkart.com and product media lives under /media/catalog/product.
  const CDN = "https://r2dkmedia.dentalkart.com";
  const MEDIA_PREFIX = "/media/catalog/product";
  const rawImage = (p.image_url || p.thumbnail_url || "").trim();
  let image = "";
  if (rawImage) {
    if (/^https?:\/\//i.test(rawImage)) {
      image = rawImage.replace(/^https?:\/\/images1\.dentalkart\.com/i, CDN);
    } else if (rawImage.startsWith("//")) {
      image = rawImage
        .replace(/^\/\/images1\.dentalkart\.com/i, CDN)
        .replace(/^\/\//, "https://");
    } else if (rawImage.startsWith("/")) {
      // Relative media path — prepend CDN + /media/catalog/product if not already included.
      image = rawImage.startsWith(MEDIA_PREFIX)
        ? `${CDN}${rawImage}`
        : `${CDN}${MEDIA_PREFIX}${rawImage}`;
    } else {
      image = `${CDN}${MEDIA_PREFIX}/${rawImage}`;
    }
  }

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
    const pdp = parsePdpHtml(html);
    if (!pdp) return null;

    const $ = cheerio.load(html);
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
      image: pdp.image,
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
