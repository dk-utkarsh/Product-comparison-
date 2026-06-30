/**
 * Generic PDP fetcher — for merchants we do NOT have a dedicated scraper for.
 *
 * The Google-Shopping "top 10 competitors" feature surfaces arbitrary merchants
 * (thedentistshop.com, Libral Traders, Dentaid, amazon.in…). We have no
 * site-specific scraper for those, so this falls back to the structured data
 * almost every storefront already ships: a JSON-LD `Product` node (and OpenGraph
 * as a secondary source), parsed by the shared `parsePdpHtml`. It will not parse
 * every site, but it covers the common Shopify/WooCommerce/Magento storefronts
 * the same way our dedicated scrapers' fallback path does.
 *
 * Returns null on any failure — the caller then shows the Google-Shopping card
 * price as a last resort.
 */
import * as cheerio from "cheerio";
import { smartFetch, scraperApiUrl } from "../http";
import { parsePdpHtml } from "../pdp";
import { detectPackSize, calculateUnitPrice } from "../pack-detector";
import { parseVariantSpec } from "../variant-spec";
import type { ProductData, ProductVariant } from "../types";

/** Fetch a URL's HTML, returning null on any non-OK / error. */
async function getHtml(url: string, timeout = 12000): Promise<string | null> {
  try {
    const res = await smartFetch(url, { timeout, retries: 1 });
    if (!res.ok) return null;
    return await res.text();
  } catch {
    return null;
  }
}

/** WooCommerce VARIABLE products embed every variation (size + price) in the page
 *  as `data-product_variations` on the variations form. Without this, a variable
 *  product (e.g. ayushidensity.com sterilization reel: 55/75/100/150/200/300 MM)
 *  returns only the DEFAULT price — the wrong sub-variant. Extracting them lets the
 *  Python `select_variant` pick the one matching the DK size. Returns [] when the
 *  page is not a WooCommerce variable product. */
function parseWooVariations(html: string): ProductVariant[] {
  const $ = cheerio.load(html);
  const raw = $("[data-product_variations]").first().attr("data-product_variations");
  if (!raw || raw === "false") return [];
  let arr: Array<Record<string, unknown>>;
  try {
    arr = JSON.parse(raw); // cheerio already HTML-entity-decodes the attribute
  } catch {
    return [];
  }
  if (!Array.isArray(arr)) return [];
  const out: ProductVariant[] = [];
  for (const v of arr) {
    const attrs = (v.attributes as Record<string, string>) || {};
    const name = Object.values(attrs).filter(Boolean).join(" ").trim();
    const price = Number(v.display_price) || 0;
    if (!name || !(price > 0)) continue;
    const packSize = detectPackSize(name);
    out.push({
      name,
      sku: String(v.sku || ""),
      price,
      mrp: Number(v.display_regular_price) || price,
      packSize,
      unitPrice: calculateUnitPrice(price, packSize),
      variantSpec: parseVariantSpec(name),
    });
  }
  return out;
}

/** Shopify VARIABLE products keep their variants in the storefront product JSON,
 *  NOT in the page HTML (the variant <select> is hydrated client-side, so a server
 *  fetch sees only the default/lowest price — e.g. buzzdent GC Gold Label 9 shows
 *  ₹1424 Mini Pack when the DK product is the ₹2858 (Extra) Big Pack). Every
 *  Shopify store exposes `/products/<handle>.json`; fetch it (direct, then
 *  ScraperAPI proxy on a datacenter-IP block) and map each variant so the Python
 *  `select_variant` can pick the child matching the DK pack. Returns [] for
 *  non-Shopify / single-variant pages. */
async function fetchShopifyVariations(url: string): Promise<ProductVariant[]> {
  const m = url.split("?")[0].match(/^(https?:\/\/[^/]+)(?:.*?)\/products\/([^/?#]+)/i);
  if (!m) return [];
  const endpoint = `${m[1]}/products/${m[2].replace(/\.(html|js|json)$/i, "")}.json`;
  let raw = await getHtml(endpoint, 12000);
  if (!raw) {
    const viaScraper = scraperApiUrl(endpoint);
    if (viaScraper) raw = await getHtml(viaScraper, 40000);
  }
  if (!raw) return [];
  let data: { product?: Record<string, unknown> } & Record<string, unknown>;
  try {
    data = JSON.parse(raw);
  } catch {
    return [];
  }
  const p = (data.product || data) as Record<string, unknown>;
  const vs = Array.isArray(p.variants) ? (p.variants as Array<Record<string, unknown>>) : [];
  if (vs.length < 2) return []; // single-variant product — nothing to choose
  const out: ProductVariant[] = [];
  for (const v of vs) {
    // The variant `title` is the option combo (e.g. "GC Gold Label 9 (Extra) Big
    // Pack"); "Default Title" means a single-option product (filtered above/in
    // select_variant). Shopify `.json` prices are major units ("2858.00").
    const name = String(v.title || "").trim();
    const price = Number(v.price) || 0;
    if (!name || !(price > 0)) continue;
    const packSize = detectPackSize(name);
    // compare_at_price is the struck-through "was" price — use it only when it's
    // actually higher (some stores ship junk like 250 for a 2858 product).
    const cmp = Number(v.compare_at_price) || 0;
    out.push({
      name,
      sku: String(v.sku || ""),
      price,
      mrp: cmp > price ? cmp : price,
      packSize,
      unitPrice: calculateUnitPrice(price, packSize),
      variantSpec: parseVariantSpec(name),
    });
  }
  return out;
}

/** Magento CONFIGURABLE products embed every option (label + price) in a
 *  `jsonConfig` object already present in the page HTML — `attributes[].options[]`
 *  carry the labels ("Big Pack - (Extra)") and `optionPrices[productId].finalPrice`
 *  the prices. Without this a Magento page yields only the base/lowest JSON-LD
 *  price (e.g. medidentalpro GC Gold Label 9 = ₹1379 Mini, when DK is the ₹2812
 *  Big Pack (Extra)). No extra request — parsed from the HTML we already have.
 *  Returns [] when the page is not a Magento configurable product. */
function parseMagentoVariations(html: string): ProductVariant[] {
  const key = html.indexOf('"jsonConfig"');
  if (key < 0) return [];
  const start = html.indexOf("{", key);
  if (start < 0) return [];
  let depth = 0;
  let end = -1;
  for (let j = start; j < html.length; j++) {
    const c = html[j];
    if (c === "{") depth++;
    else if (c === "}") {
      depth--;
      if (depth === 0) { end = j; break; }
    }
  }
  if (end < 0) return [];
  let cfg: { attributes?: Record<string, { options?: Array<Record<string, unknown>> }>;
             optionPrices?: Record<string, Record<string, { amount?: number }>> };
  try {
    cfg = JSON.parse(html.slice(start, end + 1));
  } catch {
    return [];
  }
  const optionPrices = cfg.optionPrices || {};
  const out: ProductVariant[] = [];
  const seen = new Set<string>();
  for (const attr of Object.values(cfg.attributes || {})) {
    for (const o of attr?.options || []) {
      const label = String(o.label || "").trim();
      const pid = String((Array.isArray(o.products) ? o.products[0] : "") || "");
      const pr = pid ? optionPrices[pid] : undefined;
      const price = Number(pr?.finalPrice?.amount) || 0;
      if (!label || !(price > 0) || seen.has(label.toLowerCase())) continue;
      seen.add(label.toLowerCase());
      const packSize = detectPackSize(label);
      out.push({
        name: label,
        sku: "",
        price,
        mrp: Number(pr?.oldPrice?.amount) || price,
        packSize,
        unitPrice: calculateUnitPrice(price, packSize),
        variantSpec: parseVariantSpec(label),
      });
    }
  }
  return out;
}

export async function fetchGenericProduct(url: string): Promise<ProductData | null> {
  if (!url || /^https?:\/\//i.test(url) === false) return null;

  // 1) Direct fetch + parse (now salvages malformed JSON-LD, OG/microdata price).
  let html = await getHtml(url);
  let pdp = html ? parsePdpHtml(html) : null;

  // 2) FALLBACK via ScraperAPI (proxy, no JS render) — only when the direct fetch
  //    yielded no usable product (page blocks datacenter IPs). Cheap (~1 credit)
  //    and fires solely on hard cases. JS-app (SPA) pages are NOT escalated to a
  //    rendered fetch: rendering costs ~25 credits and those pages usually ship no
  //    structured price anyway, so it would drain credits for nothing.
  if (!pdp || !pdp.name || !(pdp.price > 0)) {
    const viaScraper = scraperApiUrl(url);
    if (viaScraper) {
      const h2 = await getHtml(viaScraper, 40000);
      const sp = h2 ? parsePdpHtml(h2) : null;
      if (sp && sp.name && sp.price > 0) { pdp = sp; html = h2; }
    }
  }

  if (!pdp || !pdp.name || !(pdp.price > 0)) return null;

  // Sub-variants of a VARIABLE/CONFIGURABLE product (size/pack dropdown) — so the
  // Python variant-picker resolves to the one matching the DK product (e.g. the
  // 150MM reel, the (Extra) Big Pack), not the base/default/lowest price. Each
  // storefront platform stores them differently, so try each in turn:
  //   • WooCommerce → `data-product_variations` in the page HTML
  //   • Shopify     → `/products/<handle>.json` (HTML is JS-hydrated, no prices)
  //   • Magento     → `jsonConfig` object in the page HTML
  let variants = html ? parseWooVariations(html) : [];
  if (variants.length < 2 && html && /cdn\.shopify|Shopify\.|shopify-section/i.test(html)) {
    variants = await fetchShopifyVariations(url);
  }
  if (variants.length < 2 && html && html.includes('"jsonConfig"')) {
    variants = parseMagentoVariations(html);
  }

  // Currency guard: all our competitors are Indian (gl=in). A page that declares a
  // NON-INR currency is a foreign storefront — its price isn't comparable (e.g. a
  // Spanish "Localizador Root ZX" at €/$ that looks like a cheap ₹860). Drop it so
  // it surfaces as unverified rather than a misleading match. Empty currency =
  // assume INR (most Indian sites omit it).
  if (pdp.currency && !/^(INR|RS|₹)/.test(pdp.currency)) return null;

  const clean = url.split("?")[0].replace(/\/$/, "");
  const packSize = detectPackSize(pdp.name, pdp.description, url);
  const price = pdp.price;
  const mrp = pdp.mrp > 0 ? pdp.mrp : price;

  return {
    name: pdp.name,
    url: clean,
    image: pdp.image || "",
    price,
    mrp,
    discount: mrp > price && mrp > 0 ? Math.round(((mrp - price) / mrp) * 100) : 0,
    packaging: pdp.brand || "",
    inStock: pdp.inStock !== false,
    description: pdp.description || "",
    source: new URL(clean).hostname.replace(/^www\./, ""),
    packSize,
    unitPrice: calculateUnitPrice(price, packSize),
    sku: pdp.sku || undefined,
    // Parse the size/composition spec from the NAME + the start of the DESCRIPTION,
    // so a terse title ("GC 9 big") still picks up specs stated in the body
    // ("Powder: 15g, Liquid: …"). First 300 chars only — the product's own spec
    // block — to avoid marketing-copy numbers deeper in the text.
    variantSpec: parseVariantSpec(`${pdp.name} ${(pdp.description || "").slice(0, 300)}`),
    variants: variants.length ? variants : undefined,
  };
}
