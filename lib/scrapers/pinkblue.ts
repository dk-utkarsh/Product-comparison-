import { smartFetch } from "../http";
import * as cheerio from "cheerio";
import { ProductData, ProductVariant } from "../types";
import { detectPackSize, calculateUnitPrice } from "../pack-detector";
import { parsePdpHtml } from "../pdp";
import { parseVariantSpec } from "../variant-spec";

const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

/**
 * Scrapes Pinkblue.in search results directly.
 *
 * Pinkblue is a Magento 2 site. The search page at:
 *   https://pinkblue.in/catalogsearch/result/?q={query}
 *
 * returns server-rendered HTML with product cards. Note: the URL must use
 * `pinkblue.in` (without www) — `www.pinkblue.in` redirects to homepage.
 *
 * Each product card structure:
 *   li.item.product.product-item
 *     div.product-item-info
 *       div.product-item-photo > a[href] > img.product-image-photo.default_image[src, alt]
 *       div.product-item-details
 *         strong.product-item-name > a.product-item-link[href] — product name text
 *         div.price-box
 *           span.old-price span.price — MRP (e.g. "₹1080")
 *           span.special-price span.price — selling price (e.g. "₹666")
 *           [data-price-amount] — sometimes has numeric price
 *         div.product-label.sale-label — discount percentage (e.g. "38%")
 */
export async function searchPinkblue(
  productName: string
): Promise<ProductData[]> {
  try {
    // Use pinkblue.in without www — www redirects to homepage
    const searchUrl = `https://pinkblue.in/catalogsearch/result/?q=${encodeURIComponent(productName)}`;

    const response = await smartFetch(searchUrl);

    if (!response.ok) return [];

    const html = await response.text();

    // Verify we got search results, not the homepage
    if (
      !html.includes("catalogsearch-result-index") &&
      !html.includes("product-item-info")
    ) {
      return [];
    }

    const $ = cheerio.load(html);
    const products: ProductData[] = [];

    $("li.product-item").each((i, el) => {
      if (products.length >= 10) return;

      const $el = $(el);
      const $info = $el.find(".product-item-info").first();

      // Product name
      const name = $info
        .find(".product-item-name .product-item-link")
        .text()
        .trim();
      if (!name) return;

      // Product URL
      const url =
        $info
          .find(".product-item-name .product-item-link")
          .attr("href") || "";
      if (!url) return;

      // Image - prefer default_image class, which has the actual product photo
      const image =
        $info.find("img.product-image-photo.default_image").attr("src") || "";

      // Prices — Pinkblue uses nested span.price elements
      // MRP is in span.old-price > ... > span.price
      // Selling price is in span.special-price > ... > span.price
      // Sometimes there's also a "As low as" price with data-price-amount
      const mrpText = $info.find(".old-price .price").first().text().trim();
      const specialText = $info
        .find(".special-price .price")
        .first()
        .text()
        .trim();

      // Fallback: data-price-amount attribute (numeric)
      let dataPriceAmount = 0;
      $info.find("[data-price-amount]").each((_, priceEl) => {
        const amt = parseFloat($(priceEl).attr("data-price-amount") || "0");
        if (amt > 0 && dataPriceAmount === 0) {
          dataPriceAmount = amt;
        }
      });

      const price =
        parsePrice(specialText) || dataPriceAmount || parsePrice(mrpText);
      const mrp = parsePrice(mrpText) || price;

      if (price <= 0) return;

      // Discount from label badge
      const discountLabel = $info
        .find(".product-label.sale-label")
        .text()
        .trim();
      const discount = discountLabel
        ? parseInt(discountLabel.replace(/[^0-9]/g, ""), 10) || 0
        : mrp > price
          ? Math.round(((mrp - price) / mrp) * 100)
          : 0;

      // Key specs as description
      const spec1 = $info.find(".key-speci1").text().trim();
      const spec2 = $info.find(".key-speci2").text().trim();
      const description = [spec1, spec2].filter(Boolean).join(". ");

      const packSize = detectPackSize(name, description, url);
      const unitPrice = calculateUnitPrice(price, packSize);

      products.push({
        name,
        url,
        image,
        price,
        mrp: mrp || price,
        discount,
        packaging: "",
        inStock: true, // Pinkblue typically only shows in-stock items in search
        description,
        source: "pinkblue",
        packSize,
        unitPrice,
      });
    });

    return products;
  } catch {
    return [];
  }
}

function parsePrice(text: string): number {
  if (!text) return 0;
  const cleaned = text.replace(/[₹$,\s]/g, "").replace(/Rs\.?/gi, "");
  const match = cleaned.match(/(\d+(?:\.\d{1,2})?)/);
  if (!match) return 0;
  const num = parseFloat(match[1]);
  return isNaN(num) ? 0 : num;
}

/**
 * Parse Pinkblue's grouped-product variant table. Each `<tbody id="id_N">` row
 * carries a Variant Name, a "Package Content" cell with the composition
 * (e.g. "1 x 15 g Powder + 1 x 13.1 g (10.5mL) Liquid") and its own special /
 * regular price. Without this, the scraper only saw the listing's headline
 * (cheapest) price and matched the wrong sub-variant.
 */
function parsePinkblueVariants($: cheerio.CheerioAPI): ProductVariant[] {
  const variants: ProductVariant[] = [];
  const seen = new Set<string>();
  // Match the variant cell directly, then walk up to its row — pinkblue uses
  // several table layouts (some tbodies carry no id="id_…"), so keying on
  // `tbody[id^='id_']` silently missed the custom "variant table" pages
  // (e.g. Sure Endo Gutta Percha Points, where each size is a <tr> row).
  $('td[data-th="Variant Name"]').each((_, td) => {
    const $td = $(td);
    const vName =
      $td.find(".product-item-name").first().text().trim() || $td.text().trim();
    if (!vName || seen.has(vName)) return;

    const $row = $td.closest("tr,tbody");
    const content = $row
      .find('td[data-th="Package Content"] .product-item-name')
      .first()
      .text()
      .trim();
    const specialText = $row.find(".special-price .price").first().text().trim();
    const oldText = $row.find(".old-price .price").first().text().trim();
    const anyText = $row.find(".price").first().text().trim();
    const price = parsePrice(specialText) || parsePrice(anyText);
    const mrp = parsePrice(oldText) || price;
    if (price <= 0) return;
    seen.add(vName);

    const label = `${vName} ${content}`.trim();
    // Pack count comes from the variant NAME ("Pack of 5"); the composition
    // content ("1 x 15 g Powder") is a size, not a pack — detecting on it would
    // misread "15 g" as a 15-pack. The grams live in variantSpec instead.
    const packSize = detectPackSize(vName, "", "");
    variants.push({
      name: vName,
      sku: "",
      price,
      mrp,
      packSize,
      unitPrice: calculateUnitPrice(price, packSize),
      variantSpec: parseVariantSpec(label),
    });
  });
  return variants;
}

/**
 * Fetch a single Pinkblue PDP. Magento 2 server-rendered page.
 * JSON-LD Product block first; Magento selectors fill in description,
 * SKU and specs table when JSON-LD is thin.
 * Goes through ScraperAPI automatically when SCRAPER_API_KEY is set
 * (smartFetch handles the proxying).
 */
export async function fetchPinkblueProduct(url: string): Promise<ProductData | null> {
  try {
    const response = await smartFetch(url, { timeout: 15000 });
    if (!response.ok) return null;
    const html = await response.text();
    const pdp = parsePdpHtml(html);
    const $ = cheerio.load(html);

    const name =
      pdp?.name || $("h1.page-title span").first().text().trim();
    if (!name) return null;

    // Magento long description beats the JSON-LD one when present.
    const magentoDesc = $(".product.attribute.description .value")
      .text()
      .replace(/\s+/g, " ")
      .trim();
    const description = magentoDesc || pdp?.description || "";

    // Specs table → packaging string ("Shade: A2 | Pack: 50 pcs ...").
    const specs: string[] = [];
    $("#product-attribute-specs-table tr").each((_, tr) => {
      const label = $(tr).find("th").text().trim();
      const value = $(tr).find("td").text().trim();
      if (label && value) specs.push(`${label}: ${value}`);
    });
    const packaging = specs.join(" | ");

    const sku =
      pdp?.sku || $(".product.attribute.sku .value").first().text().trim();

    const packSize = detectPackSize(name, `${description} ${packaging}`, url);
    const variants = parsePinkblueVariants($);

    let price = pdp?.price ?? 0;
    if (!price) {
      const amt = $(".product-info-price [data-price-amount]").first().attr("data-price-amount");
      price = parseFloat(amt || "0") || 0;
    }
    if (!price) {
      // Custom "bulk price" layout (no JSON-LD, no data-price-amount) — the
      // price lives in a data-final-price attribute, e.g.
      // <span class="main-bulk-price" data-final-price="271">.
      const fp = $("[data-final-price]").first().attr("data-final-price");
      price = parseFloat(fp || "0") || 0;
    }
    if (!price && variants.length) {
      // Variant-table pages: fall back to the cheapest sub-variant price.
      price = Math.min(...variants.map((v) => v.price).filter((p) => p > 0));
    }
    const mrpText = $(".product-info-price .old-price .price").first().text().trim();
    const mrp = parsePrice(mrpText) || pdp?.mrp || price;
    if (price <= 0) return null;
    return {
      name,
      url,
      image: pdp?.image || $("img.product-image-photo").first().attr("src") || "",
      price,
      mrp,
      discount: mrp > price && mrp > 0 ? Math.round(((mrp - price) / mrp) * 100) : 0,
      packaging,
      inStock: pdp?.inStock ?? !$(".stock.unavailable").length,
      description,
      source: "pinkblue",
      packSize,
      unitPrice: calculateUnitPrice(price, packSize),
      sku: sku || undefined,
      variants: variants.length ? variants : undefined,
      variantSpec: parseVariantSpec(`${name} ${packaging} ${description}`),
    };
  } catch {
    return null;
  }
}
