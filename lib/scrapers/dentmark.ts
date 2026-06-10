import { smartFetch } from "../http";
import * as cheerio from "cheerio";
import { ProductData } from "../types";
import { detectPackSize, calculateUnitPrice } from "../pack-detector";
import { parsePdpHtml } from "../pdp";

const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

/**
 * Scrapes Dentmark.com search results.
 *
 * Dentmark is a Laravel-based site with server-rendered HTML.
 * Search URL: https://www.dentmark.com/search?user_search_type=products&searchterm={query}
 *
 * Product card structure:
 *   div.product-style.customized
 *     a[href*="/products/"] — product link
 *     a.prod-img-style > img — product image
 *     p.prod-name — product name
 *     span.prod-price — sale price (format: "INR 188")
 *     span.cut-price — MRP (format: "INR 461")
 *     span.prod-off — discount (format: "59% OFF")
 *     span.sold-out.font-pt — "Sold Out" if out of stock
 */
export async function searchDentmark(
  productName: string
): Promise<ProductData[]> {
  try {
    const searchUrl = `https://www.dentmark.com/search?user_search_type=products&searchterm=${encodeURIComponent(productName)}`;

    const response = await smartFetch(searchUrl);

    if (!response.ok) return [];

    const html = await response.text();
    const $ = cheerio.load(html);
    const products: ProductData[] = [];

    $("div.product-style.customized").each((i, el) => {
      if (products.length >= 10) return;

      const $el = $(el);

      // Product name
      const name = $el.find("p.prod-name").text().trim();
      if (!name) return;

      // Product URL
      const url = $el.find('a[href*="/products/"]').attr("href") || "";
      const fullUrl = url
        ? url.startsWith("http")
          ? url
          : `https://www.dentmark.com${url}`
        : "";

      // Image
      const image = $el.find("a.prod-img-style > img").attr("src") || "";
      const fullImage = image
        ? image.startsWith("http")
          ? image
          : `https://www.dentmark.com${image}`
        : "";

      // Sale price — format: "INR 188"
      const priceText = $el.find("span.prod-price").text().trim();
      const price = parseINRPrice(priceText);

      // MRP — format: "INR 461"
      const mrpText = $el.find("span.cut-price").text().trim();
      const mrp = parseINRPrice(mrpText) || price;

      if (price <= 0) return;

      // Discount — format: "59% OFF"
      const discountText = $el.find("span.prod-off").text().trim();
      const discount = discountText
        ? parseInt(discountText.replace(/[^0-9]/g, ""), 10) || 0
        : mrp > price
          ? Math.round(((mrp - price) / mrp) * 100)
          : 0;

      // Stock status
      const soldOutEl = $el.find("span.sold-out.font-pt");
      const inStock =
        soldOutEl.length === 0 ||
        !soldOutEl.text().toLowerCase().includes("sold out");

      const description = "";
      const packSize = detectPackSize(name, description, url);
      const unitPrice = calculateUnitPrice(price, packSize);

      products.push({
        name,
        url: fullUrl,
        image: fullImage,
        price,
        mrp: mrp || price,
        discount,
        packaging: "",
        inStock,
        description,
        source: "dentmark",
        packSize,
        unitPrice,
      });
    });

    return products;
  } catch {
    return [];
  }
}

/**
 * Fetch a single Dentmark PDP (Laravel, server-rendered).
 * JSON-LD/og-meta first, then the same price selectors the search
 * cards use (span.prod-price / span.cut-price, "INR 188" format).
 */
export async function fetchDentmarkProduct(url: string): Promise<ProductData | null> {
  try {
    const response = await smartFetch(url, { timeout: 15000 });
    if (!response.ok) return null;
    const html = await response.text();
    const pdp = parsePdpHtml(html);
    const $ = cheerio.load(html);

    const name = pdp?.name || $("h1").first().text().trim();
    if (!name) return null;

    const priceText = $("h4.prod-rate").first().text().trim()
      || $("span.prod-price").first().text().trim();
    const cutText = $("span.cut-rate").first().text().trim()
      || $("span.cut-price").first().text().trim();
    const parseInr = (t: string) => parseFloat(t.replace(/[^0-9.]/g, "")) || 0;
    const price = pdp?.price || parseInr(priceText);
    const mrp = parseInr(cutText) || pdp?.mrp || price;
    if (price <= 0) return null;

    // JSON-LD description first. Dentmark's JSON-LD description is empty and
    // its og:description is the generic site tagline ("Buy Dental Products
    // Online in India - Dentmark"), so skip that in favour of the PDP's
    // Description accordion panel (#collapseOne inside .detail-tabs).
    const siteDesc = $("#collapseOne, .detail-tabs .card-body")
      .first()
      .text()
      .replace(/\s+/g, " ")
      .trim();
    const metaDesc = pdp?.description || "";
    const isGenericMeta = /^buy dental products online/i.test(metaDesc);
    const description = (!isGenericMeta && metaDesc) || siteDesc || metaDesc;

    const packSize = detectPackSize(name, description, url);
    return {
      name,
      url,
      image: pdp?.image || $("img.prod-img-style, .product-image img").first().attr("src") || "",
      price,
      mrp,
      discount: mrp > price && mrp > 0 ? Math.round(((mrp - price) / mrp) * 100) : 0,
      packaging: pdp?.brand || "",
      inStock: pdp?.inStock ?? !/sold\s*out/i.test(html),
      description,
      source: "dentmark",
      packSize,
      unitPrice: calculateUnitPrice(price, packSize),
      sku: pdp?.sku || undefined,
    };
  } catch {
    return null;
  }
}

/**
 * Parse Dentmark price strings like "INR 188" or "INR 1,461".
 */
function parseINRPrice(text: string): number {
  if (!text) return 0;
  const cleaned = text
    .replace(/INR/gi, "")
    .replace(/[₹$,\s]/g, "")
    .replace(/Rs\.?/gi, "");
  const match = cleaned.match(/(\d+(?:\.\d{1,2})?)/);
  if (!match) return 0;
  const num = parseFloat(match[1]);
  return isNaN(num) ? 0 : num;
}
