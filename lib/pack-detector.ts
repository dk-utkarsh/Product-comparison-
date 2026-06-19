/**
 * Detects pack/quantity size from product names and descriptions.
 *
 * Common patterns in dental product names:
 *   - "Pack Of 11", "Pack of 5", "(Pack Of 11)"
 *   - "Set of 3", "Set Of 10"
 *   - "5 Pcs", "10pcs", "11 Pieces"
 *   - "Combo 5", "Combo Pack of 3"
 *   - "x5", "x 10"
 *   - "5 Units", "11 units"
 *   - "Qty: 5", "Quantity: 10"
 */
export function detectPackSize(name: string, description?: string, url?: string): number {
  // Check name + description + URL for pack info
  // URL often contains "pack-of-12" in the slug
  const urlClean = url ? url.replace(/[-_/]/g, " ") : "";
  const text = `${name} ${description || ""} ${urlClean}`.toLowerCase();

  // Also check for "Net Quantity X N" pattern (common on Indian sites)
  const netQtyMatch = text.match(/net\s*(?:quantity|qty)\s*[:\s]*(\d+)\s*n?\b/i);
  if (netQtyMatch) {
    const num = parseInt(netQtyMatch[1], 10);
    if (num >= 2 && num <= 10000) return num;
  }

  const patterns = [
    // "pack of 11", "pack of 5", "(pack of 11)"
    /pack\s*(?:of\s*)?(\d+)/i,
    // "set of 3", "set of 10"
    /set\s*(?:of\s*)?(\d+)/i,
    // "combo pack of 3", "combo 5"
    /combo\s*(?:pack\s*(?:of\s*)?)?\s*(\d+)/i,
    // "11 pcs", "5pcs", "10 pieces", "11 piece"
    /(\d+)\s*(?:pcs|pieces?|pc)\b/i,
    // "5 units", "11 units"
    /(\d+)\s*units?\b/i,
    // "x5", "x 10", "x11" — but NOT "1 x 15 g" / "x 13.1 g" / "x 10 ml"
    // (a size/measurement or a decimal, not a pack count)
    /\bx\s*(\d+)(?!\.?\d)(?!\s*(?:g|gm|gms|gram|grams|ml|mg|kg|cm|mm|oz|l)\b)/i,
    // "qty: 5", "quantity: 10"
    /(?:qty|quantity)\s*[:\-]?\s*(\d+)/i,
    // "5 nos", "11 nos"
    /(\d+)\s*nos?\b/i,
    // "100 cases", "50 cases"
    /(\d+)\s*cases?\b/i,
    // "50 brackets", "100 brackets"
    /(\d+)\s*brackets?\b/i,
    // "20 bags", "100 bags"
    /(\d+)\s*bags?\b/i,
    // "12 blades", "10 blades"
    /(\d+)\s*blades?\b/i,
    // "6 tips", "10 tips"
    /(\d+)\s*tips?\b/i,
    // "5 syringes", "10 syringes"
    /(\d+)\s*syringes?\b/i,
    // "5 strips", "10 strips"
    /(\d+)\s*strips?\b/i,
    // "100 gloves", "50 gloves"
    /(\d+)\s*gloves?\b/i,
    // "10 capsules", "20 capsules"
    /(\d+)\s*capsules?\b/i,
    // "5 cartridges"
    /(\d+)\s*cartridges?\b/i,
    // "10 refills"
    /(\d+)\s*refills?\b/i,
    // "3 tubes"
    /(\d+)\s*tubes?\b/i,
    // "5 bottles"
    /(\d+)\s*bottles?\b/i,
    // "100 crowns"
    /(\d+)\s*crowns?\b/i,
    // "Pk of 10", "Pk 10"
    /pk\s*(?:of\s*)?(\d+)/i,
  ];

  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match) {
      const num = parseInt(match[1], 10);
      // Sanity check: pack size should be between 2 and 10000
      if (num >= 2 && num <= 10000) {
        return num;
      }
    }
  }

  return 1; // default: single unit
}

/**
 * Calculates the unit price given total price and pack size.
 */
export function calculateUnitPrice(price: number, packSize: number): number {
  if (packSize <= 0 || price <= 0) return price;
  return Math.round((price / packSize) * 100) / 100;
}

/**
 * Calculates equivalent pack price for comparison.
 * If Dentalkart sells pack of 11 and competitor sells single at ₹50,
 * equivalent = ₹50 × 11 = ₹550.
 */
export function calculateEquivalentPrice(
  competitorPrice: number,
  competitorPackSize: number,
  referencePackSize: number
): number {
  if (competitorPackSize <= 0 || referencePackSize <= 0) return competitorPrice;
  const unitPrice = competitorPrice / competitorPackSize;
  return Math.round(unitPrice * referencePackSize * 100) / 100;
}
