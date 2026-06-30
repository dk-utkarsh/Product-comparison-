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
  currency: string; // ISO currency from the page (e.g. "INR"); "" if unknown
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

/* schema.org `image` may be a URL string, an array of URLs, an ImageObject
 * ({"@type":"ImageObject","url":…}), or an array of ImageObjects (WooCommerce/
 * Yoast ship the latter). A naive String() on an object yields the literal
 * "[object Object]" — a broken <img src> — so unwrap to the first usable URL. */
function jsonLdImageUrl(img: unknown): string {
  if (!img) return "";
  if (typeof img === "string") return img.trim();
  if (Array.isArray(img)) {
    for (const it of img) {
      const u = jsonLdImageUrl(it);
      if (u) return u;
    }
    return "";
  }
  if (typeof img === "object") {
    const o = img as Record<string, unknown>;
    const u = o.url ?? o.contentUrl ?? o["@id"];
    return typeof u === "string" ? u.trim() : "";
  }
  return "";
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
  // Accept "Product" AND the full-URL form "http(s)://schema.org/Product" (and
  // subtypes like "IndividualProduct") — many sites (e.g. hospitalstore.com) emit
  // the URL form, which we'd otherwise silently skip.
  const isProduct = (x: unknown): boolean =>
    typeof x === "string" && /(^|\/)(Individual)?Product$/i.test(x);
  if (isProduct(t) || (Array.isArray(t) && t.some(isProduct))) return obj;
  // A ProductGroup (or any wrapper) carries the priced Product node(s) in
  // `hasVariant[]` rather than at the top level — jaypeedent emits a ProductGroup
  // whose variants hold name/offers/image. Recurse in so we don't miss the price.
  if (obj["hasVariant"]) {
    const hit = findProductNode(obj["hasVariant"]);
    if (hit) return hit;
  }
  if (obj["@graph"]) return findProductNode(obj["@graph"]);
  return null;
}

export function parsePdpHtml(html: string): PdpData | null {
  const $ = cheerio.load(html);

  let product: Record<string, unknown> | null = null;
  $('script[type="application/ld+json"]').each((_, el) => {
    if (product) return;
    const raw = $(el).text();
    try {
      product = findProductNode(JSON.parse(raw));
    } catch {
      // Salvage the common malformations that void otherwise-good Product JSON-LD:
      // JavaScript // line comments and /* */ blocks (illegal in JSON, but real
      // sites ship them — e.g. thedentistshop's `//"name": …`) and trailing commas.
      try {
        const fixed = raw
          .replace(/\/\*[\s\S]*?\*\//g, "")     // /* block */ comments
          .replace(/^\s*\/\/.*$/gm, "")         // whole-line // comments
          .replace(/,(\s*[}\]])/g, "$1")        // trailing commas
          // eslint-disable-next-line no-control-regex
          .replace(/[\u0000-\u001f]/g, " ");
        product = findProductNode(JSON.parse(fixed));
      } catch {
        /* still malformed — keep scanning other blocks */
      }
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
  let currency = "";

  if (product) {
    const p = product as Record<string, unknown>;
    name = stripHtml(String(p.name ?? ""));
    description = stripHtml(String(p.description ?? ""));
    sku = String(p.sku ?? "");
    const b = p.brand as Record<string, unknown> | string | undefined;
    brand = typeof b === "object" && b ? String(b.name ?? "") : String(b ?? "");
    image = jsonLdImageUrl(p.image);
    const offersRaw = p.offers;
    const offer = (Array.isArray(offersRaw) ? offersRaw[0] : offersRaw) as
      | Record<string, unknown>
      | undefined;
    if (offer) {
      price = num(offer.price ?? offer.lowPrice);
      mrp = num(offer.highPrice) || price;
      currency = String(offer.priceCurrency ?? "").toUpperCase();
      const avail = String(offer.availability ?? "");
      if (avail) inStock = /InStock/i.test(avail);
    }
  }

  // og: / meta fallback for anything still missing.
  if (!name) name = $('meta[property="og:title"]').attr("content")?.trim() || "";
  if (!name) name = ($("title").first().text() || "").split(/[|–—]/)[0].trim();
  // Rendered SPA with no <title>/og:title (e.g. dentalstores.in) — the product
  // name sits in the page heading.
  if (!name) name = stripHtml($("h1").first().html() || "").trim();
  if (!description)
    description =
      $('meta[property="og:description"]').attr("content")?.trim() ||
      $('meta[name="description"]').attr("content")?.trim() ||
      "";
  if (!image) image = $('meta[property="og:image"]').attr("content")?.trim() || "";
  if (!price) price = num($('meta[property="product:price:amount"]').attr("content"));
  if (!price) price = num($('meta[property="og:price:amount"]').attr("content"));
  if (!price) price = num($('meta[itemprop="price"]').attr("content"));
  if (!price) {
    // microdata: <span itemprop="price" content="…"> or text
    const el = $('[itemprop="price"]').first();
    if (el.length) price = num(el.attr("content") || el.text());
  }
  if (!currency)
    currency = (
      $('meta[property="product:price:currency"]').attr("content") ||
      $('meta[property="og:price:currency"]').attr("content") ||
      $('[itemprop="priceCurrency"]').attr("content") ||
      ""
    ).toUpperCase();
  if (!currency) {
    // No machine-readable currency (e.g. IPG Dental, a EUR site that declares
    // none). Infer from symbols/codes in the page: ₹/Rs/INR wins (Indian, keep);
    // otherwise a clear foreign code marks it foreign so the caller can drop it.
    // "$" alone is deliberately ignored — too ambiguous (USD/AUD/CAD/loose use).
    if (/₹|\bRs\.?\b|\bINR\b/i.test(html)) currency = "INR";
    else if (/€|\bEUR\b/.test(html)) currency = "EUR";
    else if (/£|\bGBP\b/.test(html)) currency = "GBP";
    else if (/\bAED\b|\bCAD\b|\bAUD\b|\bSGD\b/.test(html)) currency = "FOREIGN";
  }

  // NOTE: we deliberately do NOT scrape a price from free body text. On real
  // pages that yields wrong numbers — it grabs the struck-through MRP or
  // concatenates adjacent digits (dentalstores.in → "2500024"). A wrong price is
  // worse than none, so a page with no STRUCTURED price stays unverified. (Reading
  // such JS-app pages correctly needs the AI extractor — not enabled.)

  if (!name) return null;
  return { name, description, sku, brand, price, mrp: mrp || price, image, inStock, currency };
}
