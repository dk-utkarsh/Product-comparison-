/**
 * Generic PDP (product detail page) parsing helpers.
 *
 * Strategy: schema.org Product JSON-LD first (most reliable), og:/meta tags
 * as fallback. Site scrapers call parsePdpHtml() and then overlay
 * site-specific selectors for fields JSON-LD misses (packaging, specs).
 */
import * as cheerio from "cheerio";

export interface PdpData {
  name: string;
  description: string;
  sku: string;
  brand: string;
  price: number;
  mrp: number;
  image: string;
  inStock: boolean | null; // null = unknown
}

function stripHtml(s: string): string {
  return s
    .replace(/<br\s*\/?>/gi, " ")
    .replace(/<\/(p|li|div|h[1-6])>/gi, ". ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/\s+/g, " ")
    .trim();
}

function num(v: unknown): number {
  const n = parseFloat(String(v ?? "").replace(/[₹$,\s]/g, ""));
  return Number.isFinite(n) && n > 0 ? n : 0;
}

/* JSON-LD can be a Product, an array, or a @graph wrapper. */
function findProductNode(node: unknown): Record<string, unknown> | null {
  if (!node || typeof node !== "object") return null;
  if (Array.isArray(node)) {
    for (const n of node) {
      const hit = findProductNode(n);
      if (hit) return hit;
    }
    return null;
  }
  const obj = node as Record<string, unknown>;
  const t = obj["@type"];
  if (t === "Product" || (Array.isArray(t) && t.includes("Product"))) return obj;
  if (obj["@graph"]) return findProductNode(obj["@graph"]);
  return null;
}

export function parsePdpHtml(html: string): PdpData | null {
  const $ = cheerio.load(html);

  let product: Record<string, unknown> | null = null;
  $('script[type="application/ld+json"]').each((_, el) => {
    if (product) return;
    try {
      product = findProductNode(JSON.parse($(el).text()));
    } catch {
      /* malformed JSON-LD block — keep scanning */
    }
  });

  let name = "";
  let description = "";
  let sku = "";
  let brand = "";
  let price = 0;
  let mrp = 0;
  let image = "";
  let inStock: boolean | null = null;

  if (product) {
    const p = product as Record<string, unknown>;
    name = stripHtml(String(p.name ?? ""));
    description = stripHtml(String(p.description ?? ""));
    sku = String(p.sku ?? "");
    const b = p.brand as Record<string, unknown> | string | undefined;
    brand = typeof b === "object" && b ? String(b.name ?? "") : String(b ?? "");
    const img = p.image;
    image = Array.isArray(img) ? String(img[0] ?? "") : String(img ?? "");
    const offersRaw = p.offers;
    const offer = (Array.isArray(offersRaw) ? offersRaw[0] : offersRaw) as
      | Record<string, unknown>
      | undefined;
    if (offer) {
      price = num(offer.price ?? offer.lowPrice);
      mrp = num(offer.highPrice) || price;
      const avail = String(offer.availability ?? "");
      if (avail) inStock = /InStock/i.test(avail);
    }
  }

  // og: / meta fallback for anything still missing.
  if (!name) name = $('meta[property="og:title"]').attr("content")?.trim() || "";
  if (!description)
    description =
      $('meta[property="og:description"]').attr("content")?.trim() ||
      $('meta[name="description"]').attr("content")?.trim() ||
      "";
  if (!image) image = $('meta[property="og:image"]').attr("content")?.trim() || "";
  if (!price) price = num($('meta[property="product:price:amount"]').attr("content"));

  if (!name) return null;
  return { name, description, sku, brand, price, mrp: mrp || price, image, inStock };
}
