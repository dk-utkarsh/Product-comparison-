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
import { smartFetch, scraperApiUrl } from "../http";
import { parsePdpHtml } from "../pdp";
import { detectPackSize, calculateUnitPrice } from "../pack-detector";
import { parseVariantSpec } from "../variant-spec";
import type { ProductData } from "../types";

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

export async function fetchGenericProduct(url: string): Promise<ProductData | null> {
  if (!url || /^https?:\/\//i.test(url) === false) return null;

  // 1) Direct fetch + parse (now salvages malformed JSON-LD, OG/microdata price).
  let html = await getHtml(url);
  let pdp = html ? parsePdpHtml(html) : null;

  // 2) FALLBACK via ScraperAPI — only when the direct attempt yielded no usable
  //    product (page blocked datacenter IPs, needs JS, or returned nothing). This
  //    spends a ScraperAPI credit, so it fires solely on the hard cases.
  if ((!pdp || !pdp.name || !(pdp.price > 0))) {
    const viaScraper = scraperApiUrl(url);
    if (viaScraper) {
      html = await getHtml(viaScraper, 40000);   // ScraperAPI is slower
      const sp = html ? parsePdpHtml(html) : null;
      if (sp && sp.name && sp.price > 0) pdp = sp;
    }
  }

  if (!pdp || !pdp.name || !(pdp.price > 0)) return null;

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
  };
}
