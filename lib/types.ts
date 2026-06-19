import type { VariantSpec } from "./variant-spec";

export interface ProductVariant {
  name: string;
  sku: string;
  price: number;
  mrp: number;
  packSize: number;
  unitPrice: number;
  variantSpec?: VariantSpec; // parsed size/composition for this specific variant
  inStock?: boolean; // per-variant stock; absent = unknown/in-stock
}

export interface ProductData {
  name: string;
  url: string;
  image: string;
  price: number;
  mrp: number;
  discount: number;
  packaging: string;
  inStock: boolean;
  description: string;
  source: string;
  packSize: number; // detected pack quantity (1 = single unit)
  unitPrice: number; // price per single unit
  sku?: string; // product SKU code (e.g., VP2382, S5083)
  variants?: ProductVariant[]; // per-SKU variants when the listing is configurable
  selectedVariantSku?: string; // which variant is currently reflected in `price` / `name`
  variantSpec?: VariantSpec; // parsed size/composition spec (powder g, liquid ml, capsules, pack, Extra line)
}

export type MatchVerdict = "confirmed" | "possible" | "variant" | "rejected";

export interface DiscoveredMatch {
  domain: string;
  name: string;
  price: number;
  mrp: number;
  url: string;
  image: string;
  inStock: boolean;
  verdict: MatchVerdict;
  confidence: number;
  reason?: string;
  variantDiff?: string;
}

export interface ComparisonResult {
  id: string;
  searchTerm: string;
  dentalkart: ProductData | null;
  competitors: Record<string, ProductData | null>;
  alerts: PriceAlert[];
  discovered: DiscoveredMatch[];
  createdAt: string;
}

export interface PriceAlert {
  type: "cheaper_competitor";
  competitor: string;
  competitorPrice: number;
  dentalkartPrice: number;
  priceDiff: number;
}

export interface SavedMatch {
  id: string;
  productName: string;
  source: string;
  matchedUrl: string;
  matchedName: string;
}

export interface CompetitorConfig {
  id: string;
  name: string;
  color: string;
  bgLight: string;
  baseUrl: string;
  domain: string;
}
