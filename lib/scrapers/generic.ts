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
import { smartFetch } from "../http";
import { parsePdpHtml } from "../pdp";
import { detectPackSize, calculateUnitPrice } from "../pack-detector";
import { parseVariantSpec } from "../variant-spec";
import type { ProductData } from "../types";

export async function fetchGenericProduct(url: string): Promise<ProductData | null> {
  if (!url || !/^https?:\/\//i.test(url)) return null;
  let html: string;
  try {
    const res = await smartFetch(url, { timeout: 12000, retries: 1 });
    if (!res.ok) return null;
    html = await res.text();
  } catch {
    return null;
  }

  const pdp = parsePdpHtml(html);
  if (!pdp || !pdp.name || !(pdp.price > 0)) return null;

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
