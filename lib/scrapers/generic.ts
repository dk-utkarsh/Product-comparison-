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

  // Sub-variants of a WooCommerce VARIABLE product (size/spec dropdown) — so the
  // Python variant-picker resolves to the one matching the DK product (e.g. the
  // 150MM reel), not the base/default price.
  const variants = html ? parseWooVariations(html) : [];

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
    variantSpec: parseVariantSpec(pdp.name),
    variants: variants.length ? variants : undefined,
  };
}
