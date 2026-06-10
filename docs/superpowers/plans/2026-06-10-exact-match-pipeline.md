# Exact-Product Matching Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Match Dentalkart products to the *exact same variant* on pinkblue/oralkart/dentmark by comparing PDP-level data (description, packaging, attributes), judged by rules + an LLM for borderline pairs, with results persisted in a match registry.

**Architecture:** Two-phase: (1) link discovery — search → triage → fetch top-candidate PDPs → structured attribute match → LLM judge for borderline → write `product_links`; (2) price refresh — re-fetch known PDP URLs only. TS scrapers gain `fetchProduct(url)` exposed via the Node sidecar; Python owns matching, registry, and orchestration.

**Tech Stack:** FastAPI + asyncpg + sentence-transformers + rapidfuzz (existing), `anthropic` SDK (new, Claude Haiku judge), TypeScript + cheerio scrapers behind the Node sidecar (existing).

**Spec:** `docs/superpowers/specs/2026-06-10-exact-match-pipeline-design.md`

---

## File structure

**Created:**
- `api/migrations/versions/0003_match_registry.py` — `products`, `competitor_products`, `product_links`, `golden_links` tables
- `lib/pdp.ts` — shared JSON-LD / og-meta PDP parser used by all scrapers
- `api/app/matching/structured.py` — `ProductRecord`, field-wise structured matcher
- `api/app/matching/llm_judge.py` — Claude Haiku borderline judge + budget
- `api/app/registry.py` — read/write `product_links` / `products` / `competitor_products`
- `api/app/pipeline.py` — per-competitor discovery + refresh orchestration
- `api/app/routes/golden.py` — golden-set labeling endpoints
- `api/scripts/eval.py` — precision/recall eval against the golden set
- Tests: `api/tests/matching/test_structured.py`, `api/tests/matching/test_llm_judge.py`, `api/tests/test_registry.py`, `api/tests/test_bridge_fetch.py`, `api/tests/routes/test_golden_route.py`

**Modified:**
- `api/app/settings.py` — judge + PDP settings
- `api/pyproject.toml` — add `anthropic`
- `lib/scrapers/{oralkart,pinkblue,dentmark,dentalkart}.ts` — add `fetch*Product(url)`
- `api/bridges/scrape-server.ts` — `GET /product?scraper=&url=`
- `api/app/scrapers/bridge.py` — `fetch_product()`
- `api/app/matching/attributes.py` — new attribute fields + rich extraction
- `api/app/routes/compare.py` — two-phase flow, new response fields
- `api/app/routes/feedback.py` — `dk_url` field, registry promote/demote
- `api/app/main.py` — register golden router
- `api/app/static/index.html` — `dk_url` in feedback payload, ⭐/∅ golden buttons
- `README.md`

Prereqs for any task that runs the API or DB tests: Postgres reachable via `DATABASE_URL` in `.env` (same as today), sidecar running for live smoke tests only.

---

### Task 1: Migration 0003 — registry tables

**Files:**
- Create: `api/migrations/versions/0003_match_registry.py`

- [ ] **Step 1: Write the migration**

```python
"""match registry: products, competitor_products, product_links, golden_links

Revision ID: 0003_match_registry
Revises: 0002_match_feedback
"""
from alembic import op

revision = "0003_match_registry"
down_revision = "0002_match_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
          id           bigserial PRIMARY KEY,
          sku          text,
          url          text NOT NULL UNIQUE,
          name         text NOT NULL,
          description  text NOT NULL DEFAULT '',
          packaging    text NOT NULL DEFAULT '',
          brand        text,
          price        numeric,
          mrp          numeric,
          pack_size    integer NOT NULL DEFAULT 1,
          variants     jsonb,
          attrs        jsonb,
          embedding    vector(384),
          scraped_at   timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS competitor_products (
          id           bigserial PRIMARY KEY,
          source       text NOT NULL,
          url          text NOT NULL UNIQUE,
          name         text NOT NULL,
          description  text NOT NULL DEFAULT '',
          packaging    text NOT NULL DEFAULT '',
          price        numeric,
          mrp          numeric,
          in_stock     boolean,
          pack_size    integer NOT NULL DEFAULT 1,
          variants     jsonb,
          attrs        jsonb,
          scraped_at   timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS product_links (
          id              bigserial PRIMARY KEY,
          product_id      bigint NOT NULL REFERENCES products(id) ON DELETE CASCADE,
          source          text NOT NULL,
          competitor_url  text NOT NULL,
          verdict         text NOT NULL,   -- confirmed | possible | variant | rejected
          confidence      double precision NOT NULL DEFAULT 0,
          matched_by      text NOT NULL,   -- rules | llm | human
          reason          text,
          llm_response    jsonb,
          status          text NOT NULL DEFAULT 'active',  -- active | human_verified | killed
          created_at      timestamptz NOT NULL DEFAULT now(),
          updated_at      timestamptz NOT NULL DEFAULT now(),
          UNIQUE (product_id, source, competitor_url)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS product_links_lookup_idx "
        "ON product_links (product_id, source, status)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS golden_links (
          id              bigserial PRIMARY KEY,
          dk_name         text NOT NULL,
          source          text NOT NULL,
          competitor_url  text,            -- NULL when label = 'no_match'
          label           text NOT NULL,   -- correct | no_match
          created_at      timestamptz NOT NULL DEFAULT now(),
          UNIQUE (dk_name, source)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS golden_links")
    op.execute("DROP TABLE IF EXISTS product_links")
    op.execute("DROP TABLE IF EXISTS competitor_products")
    op.execute("DROP TABLE IF EXISTS products")
```

- [ ] **Step 2: Run the migration**

Run: `cd api && uv run alembic upgrade head`
Expected: `Running upgrade 0002_match_feedback -> 0003_match_registry`

- [ ] **Step 3: Verify tables exist**

Run: `cd api && uv run python -c "
import asyncio, asyncpg
from app.settings import get_settings
async def main():
    c = await asyncpg.connect(get_settings().database_url)
    rows = await c.fetch(\"SELECT tablename FROM pg_tables WHERE tablename IN ('products','competitor_products','product_links','golden_links')\")
    print(sorted(r['tablename'] for r in rows))
    await c.close()
asyncio.run(main())"`
Expected: `['competitor_products', 'golden_links', 'product_links', 'products']`

- [ ] **Step 4: Commit**

```bash
git add api/migrations/versions/0003_match_registry.py
git commit -m "feat(db): products, competitor_products, product_links, golden_links tables"
```

---

### Task 2: Settings + anthropic dependency

**Files:**
- Modify: `api/app/settings.py`
- Modify: `api/pyproject.toml`
- Test: `api/tests/test_settings.py`

- [ ] **Step 1: Write the failing test** — append to `api/tests/test_settings.py`:

```python
def test_judge_and_pdp_defaults():
    from app.settings import get_settings
    s = get_settings()
    assert s.llm_judge_model == "claude-haiku-4-5"
    assert s.llm_judge_budget_per_run == 30
    assert s.pdp_top_k == 3
    assert s.confirm_cosine == 0.80
    assert s.confirm_fuzz == 0.85
    assert isinstance(s.anthropic_api_key, str)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd api && uv run pytest tests/test_settings.py::test_judge_and_pdp_defaults -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'llm_judge_model'`

- [ ] **Step 3: Implement** — in `api/app/settings.py`, add after `price_band_max_ratio`:

```python
    # ── Exact-match pipeline ────────────────────────────────
    # LLM borderline judge (Approach C). Empty key disables the judge;
    # the pipeline then runs as pure rules (Approach A).
    anthropic_api_key: str = ""
    llm_judge_model: str = "claude-haiku-4-5"
    llm_judge_budget_per_run: int = 30

    # How many top triaged candidates per competitor get a PDP fetch.
    pdp_top_k: int = 3

    # Structured-match CONFIRMED gates: product line must agree strongly.
    confirm_cosine: float = 0.80
    confirm_fuzz: float = 0.85
```

In `api/pyproject.toml`, add to `dependencies`:

```toml
  "anthropic>=0.92",
```

Run: `cd api && uv sync`

- [ ] **Step 4: Run tests**

Run: `cd api && uv run pytest tests/test_settings.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add api/app/settings.py api/pyproject.toml api/uv.lock api/tests/test_settings.py
git commit -m "feat(api): settings for LLM judge, PDP top-K, confirm gates; add anthropic dep"
```

---

### Task 3: Shared PDP parser (`lib/pdp.ts`)

Most e-commerce PDPs embed a schema.org `Product` JSON-LD block; og: meta tags are the fallback. This helper is the backbone of pinkblue/dentmark/dentalkart PDP scraping (oralkart uses Shopify's `.js` endpoint instead).

**Files:**
- Create: `lib/pdp.ts`

- [ ] **Step 1: Implement**

```typescript
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
```

- [ ] **Step 2: Typecheck**

Run: `npx tsc --noEmit`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add lib/pdp.ts
git commit -m "feat(scrapers): shared JSON-LD/og-meta PDP parser"
```

---

### Task 4: `fetchOralkartProduct` (Shopify `.js` endpoint)

**Files:**
- Modify: `lib/scrapers/oralkart.ts`

- [ ] **Step 1: Implement** — append to `lib/scrapers/oralkart.ts`:

```typescript
interface ShopifyVariantJson {
  title?: string;
  sku?: string;
  price?: number; // paise (Shopify .js returns minor units)
  compare_at_price?: number | null;
  available?: boolean;
}

interface ShopifyProductJson {
  title?: string;
  body_html?: string;
  vendor?: string;
  price?: number;
  compare_at_price?: number | null;
  available?: boolean;
  featured_image?: string;
  variants?: ShopifyVariantJson[];
}

function stripTags(html: string): string {
  return html
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Fetch a single Oralkart PDP via Shopify's product .js endpoint.
 * https://www.oralkart.com/products/{handle}.js returns full product JSON
 * with body_html (description) and per-variant prices in paise.
 */
export async function fetchOralkartProduct(url: string): Promise<ProductData | null> {
  try {
    const clean = url.split("?")[0].replace(/\/$/, "");
    const response = await smartFetch(`${clean}.js`, { accept: "application/json" });
    if (!response.ok) return null;
    const p = (await response.json()) as ShopifyProductJson;
    const name = (p.title || "").trim();
    if (!name) return null;

    const description = stripTags(p.body_html || "");
    const price = (p.price ?? 0) / 100;
    const mrp = (p.compare_at_price ?? 0) / 100 || price;
    const packSize = detectPackSize(name, description, url);

    const variants = (p.variants || []).map((v) => {
      const vPrice = (v.price ?? 0) / 100;
      const vPack = detectPackSize(v.title || name, "", "");
      return {
        name: v.title || "",
        sku: v.sku || "",
        price: vPrice,
        mrp: (v.compare_at_price ?? 0) / 100 || vPrice,
        packSize: vPack,
        unitPrice: calculateUnitPrice(vPrice, vPack),
      };
    });

    return {
      name,
      url: clean,
      image: p.featured_image
        ? p.featured_image.replace(/^\/\//, "https://")
        : "",
      price,
      mrp,
      discount: mrp > price && mrp > 0 ? Math.round(((mrp - price) / mrp) * 100) : 0,
      packaging: p.vendor || "",
      inStock: p.available !== false,
      description,
      source: "oralkart",
      packSize,
      unitPrice: calculateUnitPrice(price, packSize),
      sku: p.variants?.[0]?.sku || undefined,
      variants,
    };
  } catch {
    return null;
  }
}
```

- [ ] **Step 2: Typecheck**

Run: `npx tsc --noEmit`
Expected: no errors

- [ ] **Step 3: Live smoke test**

First grab a real product URL from search, then fetch it:

Run: `npx tsx -e "
import('./lib/scrapers/oralkart.js').then(async (m) => {
  const hits = await m.searchOralkart('composite');
  console.log('search hit:', hits[0]?.url);
  const p = await m.fetchOralkartProduct(hits[0].url);
  console.log(JSON.stringify({name: p?.name, price: p?.price, desc: p?.description?.slice(0,120), variants: p?.variants?.length}, null, 2));
});"`
Expected: a real product with non-empty `desc` and sane `price` (rupees, not paise — e.g. 675 not 67500). If price looks 100x too big, the `.js` endpoint returned rupees on this store; drop the `/100`.

- [ ] **Step 4: Commit**

```bash
git add lib/scrapers/oralkart.ts
git commit -m "feat(scrapers): oralkart PDP fetch via Shopify .js endpoint"
```

---

### Task 5: `fetchPinkblueProduct` (Magento PDP)

**Files:**
- Modify: `lib/scrapers/pinkblue.ts`

- [ ] **Step 1: Implement** — append to `lib/scrapers/pinkblue.ts` (add `import { parsePdpHtml } from "../pdp";` at the top):

```typescript
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

    let price = pdp?.price ?? 0;
    if (!price) {
      const amt = $(".product-info-price [data-price-amount]").first().attr("data-price-amount");
      price = parseFloat(amt || "0") || 0;
    }
    const mrpText = $(".product-info-price .old-price .price").first().text().trim();
    const mrp = parsePrice(mrpText) || pdp?.mrp || price;
    if (price <= 0) return null;

    const packSize = detectPackSize(name, `${description} ${packaging}`, url);
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
    };
  } catch {
    return null;
  }
}
```

- [ ] **Step 2: Typecheck**

Run: `npx tsc --noEmit`
Expected: no errors

- [ ] **Step 3: Live smoke test**

Run: `npx tsx -e "
import('./lib/scrapers/pinkblue.js').then(async (m) => {
  const hits = await m.searchPinkblue('gc fuji');
  console.log('search hit:', hits[0]?.url);
  const p = await m.fetchPinkblueProduct(hits[0].url);
  console.log(JSON.stringify({name: p?.name, price: p?.price, sku: p?.sku, packaging: p?.packaging?.slice(0,120), desc: p?.description?.slice(0,120)}, null, 2));
});"`
Expected: non-empty name + price; `desc`/`packaging` populated. If empty, dump the HTML (`curl -s <url> > /tmp/pb.html`) and adjust the Magento selectors to what the page actually serves — keep the JSON-LD path as primary.

- [ ] **Step 4: Commit**

```bash
git add lib/scrapers/pinkblue.ts
git commit -m "feat(scrapers): pinkblue PDP fetch (JSON-LD + Magento selectors)"
```

---

### Task 6: `fetchDentmarkProduct`

**Files:**
- Modify: `lib/scrapers/dentmark.ts`

- [ ] **Step 1: Implement** — append to `lib/scrapers/dentmark.ts` (add `import { parsePdpHtml } from "../pdp";` at the top):

```typescript
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

    const priceText = $("span.prod-price").first().text().trim();
    const cutText = $("span.cut-price").first().text().trim();
    const parseInr = (t: string) => parseFloat(t.replace(/[^0-9.]/g, "")) || 0;
    const price = pdp?.price || parseInr(priceText);
    const mrp = parseInr(cutText) || pdp?.mrp || price;
    if (price <= 0) return null;

    const description =
      pdp?.description ||
      $(".product-description, #description, .tab-content").first().text().replace(/\s+/g, " ").trim();

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
```

- [ ] **Step 2: Typecheck + live smoke**

Run: `npx tsc --noEmit`
Run: `npx tsx -e "
import('./lib/scrapers/dentmark.js').then(async (m) => {
  const hits = await m.searchDentmark('composite kit');
  console.log('search hit:', hits[0]?.url);
  const p = await m.fetchDentmarkProduct(hits[0].url);
  console.log(JSON.stringify({name: p?.name, price: p?.price, desc: p?.description?.slice(0,120)}, null, 2));
});"`
Expected: name + price + some description. Adjust the description selector against the real HTML if blank.

- [ ] **Step 3: Commit**

```bash
git add lib/scrapers/dentmark.ts
git commit -m "feat(scrapers): dentmark PDP fetch"
```

---

### Task 7: `fetchDentalkartProduct`

**Files:**
- Modify: `lib/scrapers/dentalkart.ts`

- [ ] **Step 1: Implement** — append to `lib/scrapers/dentalkart.ts` (add `import { parsePdpHtml } from "../pdp";` and `import * as cheerio from "cheerio";` at the top):

```typescript
/**
 * Fetch a single Dentalkart PDP (Next.js, but product pages are
 * server-rendered for SEO and carry a schema.org Product JSON-LD block).
 * Returns null when the page can't be parsed — caller falls back to the
 * (thinner) search-API data for that product.
 */
export async function fetchDentalkartProduct(url: string): Promise<ProductData | null> {
  try {
    const response = await smartFetch(url, { timeout: 15000 });
    if (!response.ok) return null;
    const html = await response.text();
    const pdp = parsePdpHtml(html);
    if (!pdp) return null;

    const $ = cheerio.load(html);
    // Long description / key-features live in the description tab; the
    // JSON-LD description is often the short one. Concatenate both.
    const longDesc = $(
      '[class*="description"], [id*="description"], [class*="product-detail"]'
    )
      .first()
      .text()
      .replace(/\s+/g, " ")
      .trim();
    const description = [pdp.description, longDesc]
      .filter(Boolean)
      .filter((d, i, a) => a.indexOf(d) === i)
      .join(". ")
      .slice(0, 4000);

    const packSize = detectPackSize(pdp.name, description, url);
    return {
      name: pdp.name,
      url,
      image: pdp.image,
      price: pdp.price,
      mrp: pdp.mrp,
      discount:
        pdp.mrp > pdp.price && pdp.mrp > 0
          ? Math.round(((pdp.mrp - pdp.price) / pdp.mrp) * 100)
          : 0,
      packaging: pdp.brand || "",
      inStock: pdp.inStock ?? true,
      description,
      source: "dentalkart",
      packSize,
      unitPrice: calculateUnitPrice(pdp.price, packSize),
      sku: pdp.sku || undefined,
    };
  } catch {
    return null;
  }
}
```

- [ ] **Step 2: Typecheck + live smoke**

Run: `npx tsc --noEmit`
Run: `npx tsx -e "
import('./lib/scrapers/dentalkart.js').then(async (m) => {
  const hits = await m.searchDentalkart('gc fuji ix');
  console.log('search hit:', hits[0]?.url);
  const p = await m.fetchDentalkartProduct(hits[0].url);
  console.log(JSON.stringify({name: p?.name, price: p?.price, sku: p?.sku, desc: p?.description?.slice(0,160)}, null, 2));
});"`
Expected: name/price/desc populated. If JSON-LD is absent on dentalkart PDPs, inspect `curl -s <url> | grep -o 'application/ld+json'` — if truly missing, extend `parsePdpHtml` og-meta fallback rather than special-casing here.

- [ ] **Step 3: Commit**

```bash
git add lib/scrapers/dentalkart.ts
git commit -m "feat(scrapers): dentalkart PDP fetch"
```

---

### Task 8: Sidecar `/product` route + Python `fetch_product`

**Files:**
- Modify: `api/bridges/scrape-server.ts`
- Modify: `api/app/scrapers/bridge.py`
- Test: `api/tests/test_bridge_fetch.py`

- [ ] **Step 1: Sidecar route** — in `api/bridges/scrape-server.ts`, add imports:

```typescript
import { fetchPinkblueProduct } from "../../lib/scrapers/pinkblue";
import { fetchOralkartProduct } from "../../lib/scrapers/oralkart";
import { fetchDentmarkProduct } from "../../lib/scrapers/dentmark";
import { fetchDentalkartProduct } from "../../lib/scrapers/dentalkart";
```

After the `scrapers` map, add:

```typescript
const productFetchers: Record<string, (url: string) => Promise<ProductData | null>> = {
  pinkblue: fetchPinkblueProduct,
  oralkart: fetchOralkartProduct,
  dentmark: fetchDentmarkProduct,
  dentalkart: fetchDentalkartProduct,
};
```

Inside the request handler, after the `health` branch, add:

```typescript
    if (path === "product") {
      const scraper = url.searchParams.get("scraper")?.trim() || "";
      const target = url.searchParams.get("url")?.trim() || "";
      const fetcher = productFetchers[scraper];
      if (!fetcher) {
        res.writeHead(404, { "content-type": "application/json" });
        res.end(JSON.stringify({ error: `no product fetcher for: ${scraper}` }));
        return;
      }
      if (!target) {
        res.writeHead(400, { "content-type": "application/json" });
        res.end(JSON.stringify({ error: "missing query param ?url=" }));
        return;
      }
      const t0 = Date.now();
      try {
        const product = await fetcher(target);
        console.log(`[product/${scraper}] ${target} → ${product ? "ok" : "null"} in ${Date.now() - t0}ms`);
        if (!product) {
          res.writeHead(404, { "content-type": "application/json" });
          res.end(JSON.stringify({ error: "could not parse PDP" }));
          return;
        }
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify(product));
      } catch (err) {
        res.writeHead(500, { "content-type": "application/json" });
        res.end(JSON.stringify({ error: (err as Error).message }));
      }
      return;
    }
```

- [ ] **Step 2: Python bridge** — append to `api/app/scrapers/bridge.py`:

```python
_PRODUCT_TIMEOUT_S = 25.0


async def fetch_product(scraper_id: str, url: str) -> CompetitorProduct | None:
    """Fetch one PDP through the sidecar. Returns None on any failure —
    callers fall back to search-result (thin) data."""
    if not url:
        return None
    client = _get_client()
    try:
        r = await client.get(
            "/product",
            params={"scraper": scraper_id, "url": url},
            timeout=_PRODUCT_TIMEOUT_S,
        )
    except (httpx.RequestError, httpx.TimeoutException):
        return None
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except ValueError:
        return None
    if not isinstance(data, dict) or not data.get("name"):
        return None
    return CompetitorProduct.from_dict(data)
```

- [ ] **Step 3: Write the test** — create `api/tests/test_bridge_fetch.py`:

```python
import asyncio

import pytest

from app.scrapers import bridge


class _Resp:
    def __init__(self, status: int, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class _StubClient:
    def __init__(self, resp):
        self._resp = resp
        self.calls: list[tuple] = []

    async def get(self, path, params=None, timeout=None):
        self.calls.append((path, params))
        return self._resp


@pytest.fixture(autouse=True)
def _reset_client(monkeypatch):
    yield
    monkeypatch.setattr(bridge, "_client", None, raising=False)


def test_fetch_product_parses_pdp(monkeypatch):
    payload = {
        "name": "GC Fuji IX GP Capsules A2",
        "url": "https://pinkblue.in/gc-fuji-ix",
        "image": "", "price": 2300, "mrp": 2500, "discount": 8,
        "packaging": "Shade: A2 | Pack: 50", "inStock": True,
        "description": "Glass ionomer restorative", "source": "pinkblue",
        "packSize": 50, "unitPrice": 46, "sku": "GC123",
    }
    stub = _StubClient(_Resp(200, payload))
    monkeypatch.setattr(bridge, "_client", stub)

    p = asyncio.run(bridge.fetch_product("pinkblue", "https://pinkblue.in/gc-fuji-ix"))
    assert p is not None
    assert p.name == "GC Fuji IX GP Capsules A2"
    assert p.pack_size == 50
    assert stub.calls[0][0] == "/product"
    assert stub.calls[0][1]["scraper"] == "pinkblue"


def test_fetch_product_404_returns_none(monkeypatch):
    monkeypatch.setattr(bridge, "_client", _StubClient(_Resp(404, {"error": "x"})))
    assert asyncio.run(bridge.fetch_product("pinkblue", "https://x")) is None


def test_fetch_product_empty_url_returns_none():
    assert asyncio.run(bridge.fetch_product("pinkblue", "")) is None
```

- [ ] **Step 4: Run tests + typecheck**

Run: `cd api && uv run pytest tests/test_bridge_fetch.py -v`
Expected: 3 PASS
Run: `npx tsc --noEmit`
Expected: no errors

- [ ] **Step 5: Live smoke (sidecar end-to-end)**

Run (terminal with sidecar running — `npm run scrape-server`):
`curl -s 'http://127.0.0.1:3100/product?scraper=oralkart&url=<url from Task 4 smoke>' | head -c 400`
Expected: JSON with `name`, `description`, `price`

- [ ] **Step 6: Commit**

```bash
git add api/bridges/scrape-server.ts api/app/scrapers/bridge.py api/tests/test_bridge_fetch.py
git commit -m "feat(api): sidecar /product route + bridge.fetch_product"
```

---

### Task 9: Rich attribute extraction

**Files:**
- Modify: `api/app/matching/attributes.py`
- Test: `api/tests/matching/test_attributes.py`

- [ ] **Step 1: Write the failing tests** — append to `api/tests/matching/test_attributes.py`:

```python
from app.matching.attributes import extract_attributes, extract_attributes_rich


def test_new_fields_default_none():
    a = extract_attributes("3M Filtek Z350")
    assert a.material is None and a.dimension is None and a.wire_form is None


def test_material_from_name():
    a = extract_attributes("OrthoMetric NiTi Thermal Archwire")
    assert a.material == "niti"


def test_dimension_pair_from_name():
    a = extract_attributes("Archwire Rectangular .017 x .025 Lower")
    assert a.dimension == "017x025"
    assert a.wire_form == "lower"


def test_rich_fills_shade_from_description_when_unambiguous():
    a = extract_attributes_rich(
        "GC Fuji IX GP Capsules",
        description="Posterior glass ionomer. Shade A2. Box of 50 capsules.",
    )
    assert a.shade == "a2"
    assert a.pack_count == 50


def test_rich_skips_ambiguous_description_values():
    # description lists every available shade -> must NOT pick one
    a = extract_attributes_rich(
        "GC Fuji IX GP Capsules",
        description="Available in shades A1, A2, A3 and B2.",
    )
    assert a.shade is None


def test_rich_never_overrides_name_attrs():
    a = extract_attributes_rich(
        "Composite A3 syringe",
        description="Also pairs well with shade A2 etch kits.",
    )
    assert a.shade == "a3"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd api && uv run pytest tests/matching/test_attributes.py -v`
Expected: FAIL — `ImportError: cannot import name 'extract_attributes_rich'`

- [ ] **Step 3: Implement** — in `api/app/matching/attributes.py`:

Add fields to the dataclass:

```python
    material: str | None = None
    dimension: str | None = None
    wire_form: str | None = None
```

Add regexes/constants after the existing ones:

```python
_DIM_PAIR_RE = re.compile(r"\b0?\.(\d{3})\s*[x×*]\s*0?\.(\d{3})\b")
_WIRE_FORM_RE = re.compile(r"\b(upper|lower)\b", re.IGNORECASE)
# Longest-first so "nickel titanium" wins over "titanium".
_MATERIALS: list[tuple[str, str]] = [
    ("nickel titanium", "niti"), ("niti", "niti"),
    ("stainless steel", "stainless steel"),
    ("tungsten carbide", "tungsten carbide"),
    ("titanium", "titanium"), ("ceramic", "ceramic"), ("zirconia", "zirconia"),
]
```

In `extract_attributes`, before the final `return`, add:

```python
    material: str | None = None
    for needle, canon in _MATERIALS:
        if needle in lower:
            material = canon
            break

    dim = _DIM_PAIR_RE.search(lower)
    dimension = f"{dim.group(1)}x{dim.group(2)}" if dim else None

    wf = _WIRE_FORM_RE.search(lower)
    wire_form = wf.group(1).lower() if wf else None
```

and pass `material=material, dimension=dimension, wire_form=wire_form` into the `Attributes(...)` constructor.

Append the rich extractor:

```python
# Variant attributes that may be recovered from description/packaging when
# the name lacks them. Filled ONLY when the extra text yields exactly one
# distinct value — descriptions often enumerate every available variant
# ("shades A1, A2, A3"), and guessing one of those would corrupt matching.
_RICH_FIELDS: tuple[str, ...] = (
    "iso_size", "shade", "concentration", "viscosity",
    "material", "dimension", "wire_form", "pack_count",
)

_FINDALL_RES: dict[str, re.Pattern[str]] = {
    "shade": _SHADE_RE,
    "concentration": _CONC_RE,
    "iso_size": _ISO_RE,
}


def _unambiguous(field: str, text: str) -> str | None:
    """Return the single distinct value of `field` in `text`, else None."""
    pat = _FINDALL_RES.get(field)
    if pat is not None:
        values = {m.lower() for m in pat.findall(text) if m}
        return values.pop() if len(values) == 1 else None
    return None


def extract_attributes_rich(
    name: str, description: str = "", packaging: str = ""
) -> Attributes:
    """Attributes from the name, with gaps filled from description+packaging.

    Name always wins. Extra text only fills a missing field when it contains
    exactly one distinct value for it (see _RICH_FIELDS note).
    """
    attrs = extract_attributes(name)
    extra = f"{description} {packaging}".strip()
    if not extra:
        return attrs
    extra_attrs = extract_attributes(extra)
    extra_lower = extra.lower()

    for field_name in _RICH_FIELDS:
        if getattr(attrs, field_name) is not None:
            continue
        unamb = _unambiguous(field_name, extra)
        if unamb is not None:
            if field_name == "iso_size":
                setattr(attrs, field_name, int(unamb))
            elif field_name == "concentration":
                setattr(attrs, field_name, float(unamb))
            else:
                setattr(attrs, field_name, unamb)
            continue
        if field_name in ("material", "dimension", "wire_form", "viscosity", "pack_count"):
            # These extractors already return one value; ambiguity is rare
            # ("upper" AND "lower" in one description is the exception).
            if field_name == "wire_form" and len(set(_WIRE_FORM_RE.findall(extra_lower))) > 1:
                continue
            setattr(attrs, field_name, getattr(extra_attrs, field_name))
    return attrs
```

- [ ] **Step 4: Run tests**

Run: `cd api && uv run pytest tests/matching/test_attributes.py tests/matching/test_score.py tests/matching/test_triage.py tests/parity -v`
Expected: all PASS (existing tests must stay green — new fields default to `None`)

- [ ] **Step 5: Commit**

```bash
git add api/app/matching/attributes.py api/tests/matching/test_attributes.py
git commit -m "feat(matching): material/dimension/wire-form attrs + rich extraction from descriptions"
```

---

### Task 10: Structured matcher (`structured.py`)

**Files:**
- Create: `api/app/matching/structured.py`
- Test: `api/tests/matching/test_structured.py`

- [ ] **Step 1: Write the failing tests** — create `api/tests/matching/test_structured.py`:

```python
from app.matching.structured import (
    ProductRecord,
    StructuredVerdict,
    structured_match,
)


def _rec(name, **kw):
    return ProductRecord(name=name, **kw)


def test_variant_attr_mismatch_rejects():
    r = structured_match(
        _rec("GC Fuji IX GP Capsules A2", description="Shade A2"),
        _rec("GC Fuji IX GP Capsules A3", description="Shade A3"),
    )
    assert r.verdict == StructuredVerdict.REJECTED
    assert any("shade" in reason for reason in r.reasons)


def test_identical_product_confirms():
    r = structured_match(
        _rec("GC Fuji IX GP Capsules A2", unit_price=46.0),
        _rec("GC Fuji IX GP Capsules A2", unit_price=44.0),
    )
    assert r.verdict == StructuredVerdict.CONFIRMED


def test_category_gate_rejects():
    r = structured_match(
        _rec("Extraction forceps lower molar"),
        _rec("Diamond bur FG round"),
    )
    assert r.verdict == StructuredVerdict.REJECTED


def test_pack_difference_never_rejects_and_sets_note():
    r = structured_match(
        _rec("GC Fuji IX GP Capsules A2 Pack of 50", pack_size=50, unit_price=46.0),
        _rec("GC Fuji IX GP Capsules A2 Pack of 10", pack_size=10, unit_price=48.0),
    )
    assert r.verdict != StructuredVerdict.REJECTED
    assert r.pack_note == "50/pack vs 10/pack"


def test_unit_price_far_outside_band_is_borderline_not_confirmed():
    r = structured_match(
        _rec("Woodpecker scaler tip G1", unit_price=250.0),
        _rec("Woodpecker scaler tip G1", unit_price=22000.0),
    )
    assert r.verdict == StructuredVerdict.BORDERLINE


def test_weak_name_overlap_is_borderline():
    r = structured_match(
        _rec("Prime Dent Composite Kit", description="Light cure composite"),
        _rec("Prime Bond Adhesive", description="Bonding agent"),
    )
    assert r.verdict in (StructuredVerdict.BORDERLINE, StructuredVerdict.REJECTED)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd api && uv run pytest tests/matching/test_structured.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.matching.structured'`

- [ ] **Step 3: Implement** — create `api/app/matching/structured.py`:

```python
"""
Structured field-wise matcher (Approach A of the exact-match spec).

Compares two rich product records (name + description + packaging) and
returns CONFIRMED / BORDERLINE / REJECTED with reasons. BORDERLINE pairs
go on to the LLM judge; the others are final.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.matching.attributes import Attributes, extract_attributes_rich
from app.matching.embed import get_embedder
from app.matching.gates import gate_check
from app.matching.normalize import normalize_for_match
from app.matching.tokens import fuzz_ratio, weighted_overlap
from app.settings import get_settings


class StructuredVerdict(StrEnum):
    CONFIRMED = "confirmed"
    BORDERLINE = "borderline"
    REJECTED = "rejected"


@dataclass(slots=True)
class ProductRecord:
    """One side of a match — built from a scraped PDP (rich) or a search
    result (thin). Empty description/packaging just means fewer signals."""
    name: str
    url: str = ""
    description: str = ""
    packaging: str = ""
    price: float = 0.0
    mrp: float = 0.0
    pack_size: int = 1
    unit_price: float = 0.0
    sku: str | None = None
    source: str = ""


@dataclass(slots=True)
class MatchFeatures:
    """Feature vector — kept flat so it can later train the light classifier."""
    cosine: float = 0.0
    token_overlap: float = 0.0
    fuzz: float = 0.0
    brand_match: bool | None = None  # None = brand unknown on a side
    attrs_compared: int = 0          # variant attrs present on BOTH sides
    pack_ratio: float = 1.0
    unit_price_ratio: float | None = None
    thin_data: bool = False          # a side had no description/packaging


@dataclass(slots=True)
class StructuredResult:
    verdict: StructuredVerdict
    features: MatchFeatures
    reasons: list[str] = field(default_factory=list)
    pack_note: str | None = None


_VARIANT_FIELDS: tuple[str, ...] = (
    "iso_size", "shade", "concentration", "taper", "slot",
    "viscosity", "material", "dimension", "wire_form",
)


def _brand_conflict(s_attrs: Attributes, c_attrs: Attributes,
                    s_name: str, c_name: str) -> bool:
    """First-token brands differ AND neither brand appears anywhere in the
    other side's name. The containment check saves 'GC Fuji IX' vs
    'Fuji IX GP by GC' from a false reject."""
    sb, cb = s_attrs.brand, c_attrs.brand
    if not sb or not cb or sb == cb:
        return False
    return sb not in c_name.lower() and cb not in s_name.lower()


def structured_match(search: ProductRecord, candidate: ProductRecord) -> StructuredResult:
    s_norm = normalize_for_match(search.name)
    c_norm = normalize_for_match(candidate.name)
    if not s_norm or not c_norm:
        return StructuredResult(
            StructuredVerdict.REJECTED, MatchFeatures(), ["empty name"])

    gate = gate_check(s_norm, c_norm)
    if not gate.passed:
        return StructuredResult(
            StructuredVerdict.REJECTED, MatchFeatures(), [gate.reason])

    s_attrs = extract_attributes_rich(search.name, search.description, search.packaging)
    c_attrs = extract_attributes_rich(candidate.name, candidate.description, candidate.packaging)

    if _brand_conflict(s_attrs, c_attrs, search.name, candidate.name):
        return StructuredResult(
            StructuredVerdict.REJECTED, MatchFeatures(brand_match=False),
            [f"brand conflict: {s_attrs.brand} vs {c_attrs.brand}"])

    # Hard rule: a variant attribute explicitly present on BOTH sides and
    # different means different variant. A2 != A3, .016 != .018.
    mismatches: list[str] = []
    compared = 0
    for f_name in _VARIANT_FIELDS:
        sv = getattr(s_attrs, f_name)
        cv = getattr(c_attrs, f_name)
        if sv is None or cv is None:
            continue
        compared += 1
        if sv != cv:
            mismatches.append(f"{f_name} mismatch: {sv} vs {cv}")
    if mismatches:
        return StructuredResult(
            StructuredVerdict.REJECTED,
            MatchFeatures(attrs_compared=compared), mismatches)

    embedder = get_embedder()
    vecs = embedder.encode_many([s_norm, c_norm])
    cosine = float(vecs[0] @ vecs[1])
    tok = weighted_overlap(s_norm, c_norm)
    fzr = fuzz_ratio(s_norm, c_norm)

    pack_note: str | None = None
    pack_ratio = 1.0
    if search.pack_size != candidate.pack_size and search.pack_size > 0 and candidate.pack_size > 0:
        pack_note = f"{search.pack_size}/pack vs {candidate.pack_size}/pack"
        pack_ratio = candidate.pack_size / search.pack_size

    settings = get_settings()
    unit_ratio: float | None = None
    in_band = True
    s_unit = search.unit_price or search.price
    c_unit = candidate.unit_price or candidate.price
    if s_unit > 0 and c_unit > 0:
        unit_ratio = c_unit / s_unit
        max_ratio = settings.price_band_max_ratio
        in_band = (1.0 / max_ratio) <= unit_ratio <= max_ratio

    thin = not (search.description or search.packaging) or not (
        candidate.description or candidate.packaging)

    features = MatchFeatures(
        cosine=cosine, token_overlap=tok, fuzz=fzr,
        brand_match=(s_attrs.brand == c_attrs.brand) if s_attrs.brand and c_attrs.brand else None,
        attrs_compared=compared, pack_ratio=pack_ratio,
        unit_price_ratio=unit_ratio, thin_data=thin,
    )
    reasons = [
        f"cosine={cosine:.3f}", f"token={tok:.2f}", f"fuzz={fzr:.2f}",
        f"attrs_compared={compared}",
    ]
    if unit_ratio is not None:
        reasons.append(f"unit_price_ratio={unit_ratio:.2f}")

    strong_line = cosine >= settings.confirm_cosine or fzr >= settings.confirm_fuzz
    brand_ok = features.brand_match is not False
    # Thin data (no description/packaging on a side) normally blocks CONFIRMED,
    # but near-identical names with an agreeing variant attr are safe anyway.
    data_ok = (not thin) or (compared >= 1 and fzr >= 0.95)
    if strong_line and brand_ok and in_band and data_ok and (compared >= 1 or cosine >= 0.85):
        return StructuredResult(StructuredVerdict.CONFIRMED, features, reasons, pack_note)

    if not in_band:
        reasons.append("unit price outside band")
    if thin:
        reasons.append("thin data on one side")
    return StructuredResult(StructuredVerdict.BORDERLINE, features, reasons, pack_note)
```

- [ ] **Step 4: Run tests**

Run: `cd api && uv run pytest tests/matching/test_structured.py -v`
Expected: 6 PASS

- [ ] **Step 5: Lint + typecheck, commit**

Run: `cd api && uv run ruff check app/matching/structured.py && uv run mypy app/matching/structured.py`

```bash
git add api/app/matching/structured.py api/tests/matching/test_structured.py
git commit -m "feat(matching): structured field-wise matcher with CONFIRMED/BORDERLINE/REJECTED"
```

---

### Task 11: LLM judge (`llm_judge.py`)

**Files:**
- Create: `api/app/matching/llm_judge.py`
- Test: `api/tests/matching/test_llm_judge.py`

- [ ] **Step 1: Write the failing tests** — create `api/tests/matching/test_llm_judge.py`:

```python
import asyncio
import json

import pytest

from app.matching import llm_judge
from app.matching.llm_judge import JudgeBudget, judge_pair
from app.matching.structured import ProductRecord


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, payload):
        self.content = [_Block(json.dumps(payload))]


class _Messages:
    def __init__(self, payload):
        self._payload = payload
        self.calls = 0

    async def create(self, **kw):
        self.calls += 1
        return _Resp(self._payload)


class _StubClient:
    def __init__(self, payload):
        self.messages = _Messages(payload)


@pytest.fixture
def _with_key(monkeypatch):
    from app.settings import get_settings
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "sk-test")
    yield
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "")


def _records():
    return (
        ProductRecord(name="GC Fuji IX A2", description="glass ionomer"),
        ProductRecord(name="Fuji 9 GP shade A2", description="glass ionomer caps"),
    )


def test_judge_parses_structured_verdict(monkeypatch, _with_key):
    stub = _StubClient({
        "same_product": True, "same_variant": True,
        "differences": [], "confidence": 0.92, "reason": "same GI capsules",
    })
    monkeypatch.setattr(llm_judge, "_client", stub)
    s, c = _records()
    v = asyncio.run(judge_pair(s, c, JudgeBudget(5)))
    assert v is not None and v.same_product and v.same_variant
    assert v.confidence == 0.92


def test_budget_exhausted_returns_none(monkeypatch, _with_key):
    stub = _StubClient({"same_product": True, "same_variant": True,
                        "differences": [], "confidence": 1, "reason": ""})
    monkeypatch.setattr(llm_judge, "_client", stub)
    budget = JudgeBudget(0)
    s, c = _records()
    assert asyncio.run(judge_pair(s, c, budget)) is None
    assert stub.messages.calls == 0


def test_no_api_key_returns_none(monkeypatch):
    from app.settings import get_settings
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "")
    s, c = _records()
    assert asyncio.run(judge_pair(s, c, JudgeBudget(5))) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `cd api && uv run pytest tests/matching/test_llm_judge.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.matching.llm_judge'`

- [ ] **Step 3: Implement** — create `api/app/matching/llm_judge.py`:

```python
"""
LLM borderline judge (Approach C of the exact-match spec).

Claude Haiku decides "same exact product? same variant?" for pairs the
structured matcher couldn't settle. Strict JSON via structured outputs.
Every failure path degrades to None — the caller maps that to POSSIBLE,
never to a silent CONFIRMED.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import anthropic

from app.matching.structured import ProductRecord
from app.settings import get_settings

_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=get_settings().anthropic_api_key)
    return _client


@dataclass(slots=True)
class JudgeVerdict:
    same_product: bool
    same_variant: bool
    differences: list[str]
    confidence: float
    reason: str


class JudgeBudget:
    """Per-run cap on judge calls. take() returns False once exhausted."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def take(self) -> bool:
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "same_product": {"type": "boolean"},
        "same_variant": {"type": "boolean"},
        "differences": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["same_product", "same_variant", "differences", "confidence", "reason"],
    "additionalProperties": False,
}


def _render(label: str, r: ProductRecord) -> str:
    parts = [f"{label} name: {r.name}"]
    if r.sku:
        parts.append(f"{label} sku: {r.sku}")
    if r.packaging:
        parts.append(f"{label} packaging: {r.packaging[:400]}")
    if r.description:
        parts.append(f"{label} description: {r.description[:800]}")
    if r.pack_size > 1:
        parts.append(f"{label} pack size: {r.pack_size}")
    if r.unit_price > 0:
        parts.append(f"{label} unit price: INR {r.unit_price:.2f}")
    return "\n".join(parts)


def _prompt(search: ProductRecord, candidate: ProductRecord) -> str:
    return (
        "You are a dental-products catalog expert. Decide whether these two "
        "listings (from different Indian dental e-commerce sites) are the SAME "
        "exact product, and whether they are the same VARIANT (same shade, "
        "size, dimension, concentration, type). Pack quantity differences do "
        "NOT make a different variant. Be strict: if the variant cannot be "
        "established as identical, same_variant is false.\n\n"
        f"{_render('A', search)}\n\n{_render('B', candidate)}\n\n"
        "confidence is 0..1. differences lists concrete attribute differences. "
        "reason is one short sentence."
    )


async def judge_pair(
    search: ProductRecord, candidate: ProductRecord, budget: JudgeBudget
) -> JudgeVerdict | None:
    settings = get_settings()
    if not settings.anthropic_api_key:
        return None
    if not budget.take():
        return None
    try:
        resp = await _get_client().messages.create(
            model=settings.llm_judge_model,
            max_tokens=1024,
            output_config={"format": {"type": "json_schema", "schema": _JUDGE_SCHEMA}},
            messages=[{"role": "user", "content": _prompt(search, candidate)}],
        )
    except anthropic.APIError:
        return None
    text = "".join(
        b.text for b in resp.content if getattr(b, "type", "") == "text"
    )
    try:
        data = json.loads(text)
    except ValueError:
        return None
    try:
        return JudgeVerdict(
            same_product=bool(data["same_product"]),
            same_variant=bool(data["same_variant"]),
            differences=[str(d) for d in data.get("differences", [])],
            confidence=float(data["confidence"]),
            reason=str(data.get("reason", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None
```

- [ ] **Step 4: Run tests**

Run: `cd api && uv run pytest tests/matching/test_llm_judge.py -v`
Expected: 3 PASS

- [ ] **Step 5: Live smoke (optional, needs ANTHROPIC_API_KEY in .env)**

Run: `cd api && uv run python -c "
import asyncio
from app.matching.llm_judge import judge_pair, JudgeBudget
from app.matching.structured import ProductRecord
v = asyncio.run(judge_pair(
    ProductRecord(name='GC Fuji IX GP Extra Capsules A2', description='Glass ionomer posterior restorative, box of 50 capsules'),
    ProductRecord(name='GC Fuji 9 GP Extra Caps Shade A2 50/pk', description='Packable glass ionomer'),
    JudgeBudget(1)))
print(v)"`
Expected: `JudgeVerdict(same_product=True, same_variant=True, ...)`

- [ ] **Step 6: Commit**

```bash
git add api/app/matching/llm_judge.py api/tests/matching/test_llm_judge.py
git commit -m "feat(matching): Claude Haiku borderline judge with budget + graceful degradation"
```

---

### Task 12: Registry (`registry.py`)

These tests hit the real Postgres, same pattern as `tests/routes/test_feedback_route.py`.

**Files:**
- Create: `api/app/registry.py`
- Test: `api/tests/test_registry.py`

- [ ] **Step 1: Write the failing tests** — create `api/tests/test_registry.py`:

```python
import asyncio

import pytest

from app import registry
from app.db import get_db
from app.matching.structured import ProductRecord


@pytest.fixture(autouse=True)
def _clean():
    async def _wipe():
        db = await get_db()
        try:
            await db.execute("DELETE FROM product_links")
            await db.execute("DELETE FROM products")
            await db.execute("DELETE FROM competitor_products")
        finally:
            await db.close()
    asyncio.run(_wipe())
    yield
    asyncio.run(_wipe())


def _dk_record():
    return ProductRecord(
        name="GC Fuji IX GP Capsules A2", url="https://www.dentalkart.com/gc-fuji-ix.html",
        description="Glass ionomer", packaging="GC", price=2297, mrp=2500,
        pack_size=50, unit_price=45.9, sku="GC123", source="dentalkart",
    )


def test_upsert_product_is_idempotent():
    async def run():
        db = await get_db()
        try:
            pid1 = await registry.upsert_product(db, _dk_record())
            pid2 = await registry.upsert_product(db, _dk_record())
            return pid1, pid2
        finally:
            await db.close()
    pid1, pid2 = asyncio.run(run())
    assert pid1 is not None and pid1 == pid2


def test_link_roundtrip_and_status_protection():
    async def run():
        db = await get_db()
        try:
            pid = await registry.upsert_product(db, _dk_record())
            await registry.upsert_link(
                db, pid, "pinkblue", "https://pinkblue.in/fuji-ix",
                verdict="confirmed", confidence=0.9, matched_by="rules",
                reason="all attrs equal", llm_response=None,
            )
            links = await registry.get_active_links(db, pid, "pinkblue")
            # human verification survives a later rules re-write
            await registry.set_link_status(
                db, "https://www.dentalkart.com/gc-fuji-ix.html",
                "pinkblue", "https://pinkblue.in/fuji-ix", "human_verified")
            await registry.upsert_link(
                db, pid, "pinkblue", "https://pinkblue.in/fuji-ix",
                verdict="possible", confidence=0.4, matched_by="rules",
                reason="re-run", llm_response=None,
            )
            links2 = await registry.get_active_links(db, pid, "pinkblue")
            return links, links2
        finally:
            await db.close()
    links, links2 = asyncio.run(run())
    assert len(links) == 1 and links[0].verdict == "confirmed"
    assert links2[0].status == "human_verified"
    assert links2[0].verdict == "confirmed"  # not downgraded


def test_killed_links_are_excluded():
    async def run():
        db = await get_db()
        try:
            pid = await registry.upsert_product(db, _dk_record())
            await registry.upsert_link(
                db, pid, "pinkblue", "https://pinkblue.in/wrong",
                verdict="confirmed", confidence=0.9, matched_by="rules",
                reason="", llm_response=None)
            await registry.set_link_status(
                db, "https://www.dentalkart.com/gc-fuji-ix.html",
                "pinkblue", "https://pinkblue.in/wrong", "killed")
            return await registry.get_active_links(db, pid, "pinkblue")
        finally:
            await db.close()
    assert asyncio.run(run()) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `cd api && uv run pytest tests/test_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.registry'` (note: `from app import registry` fails at import)

- [ ] **Step 3: Implement** — create `api/app/registry.py`:

```python
"""
Persistent match registry — products, competitor_products, product_links.

Each function takes an open Database and is safe to call with degraded
expectations: callers wrap registry usage in try/except and fall back to
stateless matching when the DB is unavailable.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from app.db import Database
from app.matching.structured import ProductRecord


@dataclass(slots=True)
class Link:
    id: int
    product_id: int
    source: str
    competitor_url: str
    verdict: str
    confidence: float
    matched_by: str
    reason: str | None
    status: str


async def upsert_product(db: Database, rec: ProductRecord) -> int | None:
    """Insert/refresh a scraped Dentalkart PDP keyed by URL. Returns id."""
    if not rec.url:
        return None
    row = await db.fetchrow(
        """
        INSERT INTO products (sku, url, name, description, packaging,
                              price, mrp, pack_size, scraped_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8, now())
        ON CONFLICT (url) DO UPDATE SET
          name = EXCLUDED.name, description = EXCLUDED.description,
          packaging = EXCLUDED.packaging, price = EXCLUDED.price,
          mrp = EXCLUDED.mrp, pack_size = EXCLUDED.pack_size,
          sku = EXCLUDED.sku, scraped_at = now()
        RETURNING id
        """,
        rec.sku, rec.url, rec.name, rec.description, rec.packaging,
        rec.price, rec.mrp, rec.pack_size,
    )
    return int(row["id"]) if row else None


async def upsert_competitor_product(db: Database, rec: ProductRecord) -> None:
    if not rec.url:
        return
    await db.execute(
        """
        INSERT INTO competitor_products (source, url, name, description,
                                         packaging, price, mrp, pack_size, scraped_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8, now())
        ON CONFLICT (url) DO UPDATE SET
          name = EXCLUDED.name, description = EXCLUDED.description,
          packaging = EXCLUDED.packaging, price = EXCLUDED.price,
          mrp = EXCLUDED.mrp, pack_size = EXCLUDED.pack_size, scraped_at = now()
        """,
        rec.source, rec.url, rec.name, rec.description, rec.packaging,
        rec.price, rec.mrp, rec.pack_size,
    )


async def upsert_link(
    db: Database, product_id: int, source: str, competitor_url: str, *,
    verdict: str, confidence: float, matched_by: str,
    reason: str | None, llm_response: dict | None,
) -> None:
    """Write a match decision. NEVER touches rows a human has settled
    (status human_verified/killed stay as-is, including their verdict)."""
    await db.execute(
        """
        INSERT INTO product_links (product_id, source, competitor_url, verdict,
                                   confidence, matched_by, reason, llm_response)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        ON CONFLICT (product_id, source, competitor_url) DO UPDATE SET
          verdict = EXCLUDED.verdict, confidence = EXCLUDED.confidence,
          matched_by = EXCLUDED.matched_by, reason = EXCLUDED.reason,
          llm_response = COALESCE(EXCLUDED.llm_response, product_links.llm_response),
          updated_at = now()
        WHERE product_links.status = 'active'
        """,
        product_id, source, competitor_url, verdict, confidence,
        matched_by, reason, json.dumps(llm_response) if llm_response else None,
    )


async def get_active_links(db: Database, product_id: int, source: str) -> list[Link]:
    """Usable links for refresh: not killed, verdict confirmed/variant/possible."""
    rows = await db.fetch(
        """
        SELECT id, product_id, source, competitor_url, verdict, confidence,
               matched_by, reason, status
        FROM product_links
        WHERE product_id = $1 AND source = $2 AND status != 'killed'
          AND verdict IN ('confirmed', 'variant', 'possible')
        ORDER BY (status = 'human_verified') DESC, confidence DESC
        """,
        product_id, source,
    )
    return [
        Link(
            id=int(r["id"]), product_id=int(r["product_id"]), source=r["source"],
            competitor_url=r["competitor_url"], verdict=r["verdict"],
            confidence=float(r["confidence"]), matched_by=r["matched_by"],
            reason=r["reason"], status=r["status"],
        )
        for r in rows
    ]


async def find_product_id(db: Database, dk_url: str) -> int | None:
    row = await db.fetchrow("SELECT id FROM products WHERE url = $1", dk_url)
    return int(row["id"]) if row else None


async def set_link_status(
    db: Database, dk_url: str, source: str, competitor_url: str, status: str
) -> bool:
    """Feedback hook: 'human_verified' (👍) or 'killed' (👎), keyed by the
    Dentalkart product URL the UI saw. Returns True when a row changed."""
    pid = await find_product_id(db, dk_url)
    if pid is None:
        return False
    result = await db.execute(
        """
        UPDATE product_links
        SET status = $4, matched_by = 'human', updated_at = now()
        WHERE product_id = $1 AND source = $2 AND competitor_url = $3
        """,
        pid, source, competitor_url, status,
    )
    return result.endswith("1")


def needs_jsonb_codec() -> None:
    """asyncpg sends jsonb as text by default with json.dumps above — no
    codec registration required."""
```

Note: `upsert_link` passes `llm_response` as a JSON string into a `jsonb` column — asyncpg casts text → jsonb implicitly with `$8::jsonb`? It does not. If the insert fails with `DataError: invalid input for query argument $8`, change the SQL placeholder to `$8::jsonb` (both in INSERT and keep the COALESCE). Run the tests to find out — the test suite covers this path via `llm_response=None`; add one upsert with `llm_response={"x": 1}` to `test_link_roundtrip_and_status_protection` if not covered.

- [ ] **Step 4: Run tests**

Run: `cd api && uv run pytest tests/test_registry.py -v`
Expected: 3 PASS (fix the `::jsonb` cast if the DataError above appears)

- [ ] **Step 5: Lint, typecheck, commit**

Run: `cd api && uv run ruff check app/registry.py && uv run mypy app/registry.py`

```bash
git add api/app/registry.py api/tests/test_registry.py
git commit -m "feat(api): persistent match registry with human-status protection"
```

---

### Task 13: Pipeline orchestration (`pipeline.py`)

**Files:**
- Create: `api/app/pipeline.py`

This module is exercised end-to-end by the compare-route tests in Task 14 (its only consumer), so the failing test for it lives there. Implement it now; verify by import + typecheck.

- [ ] **Step 1: Implement** — create `api/app/pipeline.py`:

```python
"""
Two-phase per-competitor matching pipeline.

discover(): search -> name triage -> top-K PDP fetch -> structured match
            -> LLM judge for borderline -> best cell + link writes.
refresh():  re-fetch a known link's PDP for a fresh price.

All registry/DB writes are best-effort: a dead DB degrades to stateless
discovery, never to an error.
"""
from __future__ import annotations

import asyncio
import structlog
from dataclasses import dataclass

from app import registry
from app.db import Database
from app.matching.llm_judge import JudgeBudget, JudgeVerdict, judge_pair
from app.matching.score import Verdict
from app.matching.structured import (
    ProductRecord,
    StructuredVerdict,
    structured_match,
)
from app.matching.tokens import distinguishing_tokens
from app.matching.triage import TriageResult, triage_batch
from app.scrapers.bridge import CompetitorProduct, fetch_product, scrape_competitor
from app.settings import get_settings

log = structlog.get_logger()


@dataclass(slots=True)
class Cell:
    """One competitor cell of the result row."""
    candidate: CompetitorProduct | None
    verdict: str | None          # confirmed | possible | variant | None
    confidence: float
    reasons: list[str]
    matched_by: str | None       # rules | llm | registry | None
    pack_note: str | None
    candidates_seen: int


def record_from(cp: CompetitorProduct) -> ProductRecord:
    return ProductRecord(
        name=cp.name, url=cp.url, description=cp.description,
        packaging=cp.packaging, price=cp.price, mrp=cp.mrp,
        pack_size=cp.pack_size, unit_price=cp.unit_price,
        sku=cp.sku, source=cp.source,
    )


async def scrape_all_queries(competitor_id: str, queries: list[str]) -> list[CompetitorProduct]:
    """Fire every query in parallel, pool unique candidates by URL."""
    raws = await asyncio.gather(
        *(scrape_competitor(competitor_id, q) for q in queries),
        return_exceptions=True,
    )
    seen: set[str] = set()
    pooled: list[CompetitorProduct] = []
    for r in raws:
        if not isinstance(r, list):
            continue
        for cand in r:
            key = cand.url or cand.name
            if key and key not in seen:
                seen.add(key)
                pooled.append(cand)
    return pooled


def _prefilter(search: str, candidates: list[CompetitorProduct]) -> list[CompetitorProduct]:
    sig = distinguishing_tokens(search)
    if not sig:
        return candidates
    return [c for c in candidates if c.name and distinguishing_tokens(c.name) & sig]


def _top_candidates(
    dk_name: str, pool: list[CompetitorProduct]
) -> list[tuple[CompetitorProduct, TriageResult]]:
    """Cheap name triage; keep the K most plausible for PDP fetching."""
    settings = get_settings()
    results = triage_batch(dk_name, [c.name for c in pool])
    scored = [
        (c, r) for c, r in zip(pool, results, strict=True)
        if r.verdict != Verdict.REJECTED and r.score >= settings.variant_threshold
    ]
    scored.sort(key=lambda cr: cr[1].score, reverse=True)
    return scored[: settings.pdp_top_k]


def _judge_to_cell_verdict(jv: JudgeVerdict) -> str | None:
    if jv.same_product and jv.same_variant:
        return "confirmed"
    if jv.same_product:
        return "variant"
    return None  # rejected


_VERDICT_RANK = {"confirmed": 3, "possible": 2, "variant": 1}


async def discover(
    competitor_id: str,
    queries: list[str],
    dk_record: ProductRecord,
    *,
    budget: JudgeBudget,
    db: Database | None,
    product_id: int | None,
) -> Cell:
    pooled = await scrape_all_queries(competitor_id, queries)
    pool = _prefilter(dk_record.name, [c for c in pooled if c.name and c.price > 0])
    if not pool:
        return Cell(None, None, 0.0, [], None, None, len(pooled))

    best: Cell | None = None
    for cand, tri in _top_candidates(dk_record.name, pool):
        pdp = await fetch_product(competitor_id, cand.url)
        rich = pdp or cand  # thin fallback: search-card data only
        rec = record_from(rich)
        sm = structured_match(dk_record, rec)

        verdict: str | None = None
        matched_by: str | None = None
        confidence = 0.0
        reasons = list(sm.reasons)
        llm_response: dict | None = None

        if sm.verdict == StructuredVerdict.REJECTED:
            verdict, matched_by = "rejected", "rules"
        elif sm.verdict == StructuredVerdict.CONFIRMED:
            verdict, matched_by, confidence = "confirmed", "rules", tri.score
        else:  # BORDERLINE
            jv = await judge_pair(dk_record, rec, budget)
            if jv is None:
                # judge off/exhausted/down -> unresolved
                verdict, matched_by, confidence = "possible", "rules", tri.score
                reasons.append("needs review (judge unavailable)")
            else:
                mapped = _judge_to_cell_verdict(jv)
                verdict = mapped or "rejected"
                matched_by, confidence = "llm", jv.confidence
                reasons.append(f"judge: {jv.reason}")
                llm_response = {
                    "same_product": jv.same_product, "same_variant": jv.same_variant,
                    "differences": jv.differences, "confidence": jv.confidence,
                    "reason": jv.reason,
                }

        if sm.features.thin_data and verdict == "confirmed":
            verdict = "possible"  # thin data caps confidence per spec
            reasons.append("capped: thin data")

        # Persist every decision (best-effort).
        if db is not None and product_id is not None and verdict is not None:
            try:
                await registry.upsert_competitor_product(db, rec)
                await registry.upsert_link(
                    db, product_id, competitor_id, rich.url,
                    verdict=verdict, confidence=confidence,
                    matched_by=matched_by or "rules",
                    reason="; ".join(reasons)[:500], llm_response=llm_response,
                )
            except Exception:  # noqa: BLE001 — registry is best-effort
                log.warning("registry write failed", competitor=competitor_id)

        if verdict in _VERDICT_RANK:
            cell = Cell(rich, verdict, confidence, reasons, matched_by,
                        sm.pack_note, len(pooled))
            if best is None or (
                (_VERDICT_RANK[verdict], confidence)
                > (_VERDICT_RANK[best.verdict or ""], best.confidence)
            ):
                best = cell

    return best or Cell(None, None, 0.0, [], None, None, len(pooled))


async def refresh(competitor_id: str, link: registry.Link) -> Cell | None:
    """Re-scrape a known link's PDP for a fresh price. None -> caller
    falls back to discovery for this competitor."""
    pdp = await fetch_product(competitor_id, link.competitor_url)
    if pdp is None or pdp.price <= 0:
        return None
    return Cell(
        candidate=pdp, verdict=link.verdict, confidence=link.confidence,
        reasons=[f"registry ({link.status})", link.reason or ""],
        matched_by="registry", pack_note=None, candidates_seen=0,
    )
```

- [ ] **Step 2: Import + lint + typecheck**

Run: `cd api && uv run python -c "import app.pipeline" && uv run ruff check app/pipeline.py && uv run mypy app/pipeline.py`
Expected: clean

- [ ] **Step 3: Commit**

```bash
git add api/app/pipeline.py
git commit -m "feat(api): discovery/refresh pipeline with registry writes and judge integration"
```

---

### Task 14: Rework `/compare` to the two-phase flow

**Files:**
- Modify: `api/app/routes/compare.py`
- Test: `api/tests/routes/test_compare_route.py` (create)

- [ ] **Step 1: Write the failing test** — create `api/tests/routes/test_compare_route.py`:

```python
"""End-to-end /compare/single with the bridge and judge stubbed out.
Hits the real DB (like the feedback tests) for the registry side."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app import pipeline
from app.db import get_db
from app.main import app
from app.scrapers import bridge
from app.scrapers.bridge import CompetitorProduct


def _cp(name, url, price, source, description="", packaging="", pack_size=1):
    return CompetitorProduct(
        name=name, url=url, image="", price=price, mrp=price, discount=0,
        packaging=packaging, in_stock=True, description=description,
        source=source, pack_size=pack_size,
        unit_price=price / max(pack_size, 1), sku=None,
    )


DK_SEARCH = [_cp("GC Fuji IX GP Capsules A2", "https://www.dentalkart.com/fuji-ix.html",
                 2297, "dentalkart", description="Glass ionomer")]
PB_SEARCH = [_cp("GC Fuji 9 GP Caps Shade A2", "https://pinkblue.in/fuji-ix", 2236,
                 "pinkblue")]
PB_PDP = _cp("GC Fuji 9 GP Caps Shade A2", "https://pinkblue.in/fuji-ix", 2236,
             "pinkblue", description="Glass ionomer capsules, shade A2",
             packaging="Shade: A2")


@pytest.fixture(autouse=True)
def _clean_registry():
    async def _wipe():
        db = await get_db()
        try:
            await db.execute("DELETE FROM product_links")
            await db.execute("DELETE FROM products")
            await db.execute("DELETE FROM competitor_products")
        finally:
            await db.close()
    asyncio.run(_wipe())
    yield
    asyncio.run(_wipe())


@pytest.fixture(autouse=True)
def _stub_scrapers(monkeypatch):
    async def fake_search(cid, query):
        if cid == "dentalkart":
            return list(DK_SEARCH)
        if cid == "pinkblue":
            return list(PB_SEARCH)
        return []

    async def fake_pdp(cid, url):
        if cid == "pinkblue" and url == "https://pinkblue.in/fuji-ix":
            return PB_PDP
        if cid == "dentalkart":
            return DK_SEARCH[0]
        return None

    # compare.py and pipeline.py both import these names — patch every site.
    monkeypatch.setattr(bridge, "scrape_competitor", fake_search)
    monkeypatch.setattr(bridge, "fetch_product", fake_pdp)
    monkeypatch.setattr(pipeline, "scrape_competitor", fake_search)
    monkeypatch.setattr(pipeline, "fetch_product", fake_pdp)
    import app.routes.compare as compare_mod
    monkeypatch.setattr(compare_mod, "scrape_competitor", fake_search)
    monkeypatch.setattr(compare_mod, "fetch_product", fake_pdp, raising=False)


def test_compare_single_matches_and_persists_link():
    client = TestClient(app)
    res = client.post("/compare/single", json={"name": "GC Fuji IX GP Capsules A2"})
    assert res.status_code == 200
    j = res.json()
    pb = next(c for c in j["competitors"] if c["competitor_id"] == "pinkblue")
    assert pb["matched_url"] == "https://pinkblue.in/fuji-ix"
    assert pb["verdict"] in ("confirmed", "possible")
    assert pb["matched_by"] in ("rules", "llm")

    async def count_links():
        db = await get_db()
        try:
            row = await db.fetchrow("SELECT count(*) AS c FROM product_links")
            return int(row["c"])
        finally:
            await db.close()
    assert asyncio.run(count_links()) >= 1


def test_compare_single_uses_registry_on_second_run():
    client = TestClient(app)
    client.post("/compare/single", json={"name": "GC Fuji IX GP Capsules A2"})
    res2 = client.post("/compare/single", json={"name": "GC Fuji IX GP Capsules A2"})
    pb = next(c for c in res2.json()["competitors"] if c["competitor_id"] == "pinkblue")
    assert pb["matched_by"] == "registry"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd api && uv run pytest tests/routes/test_compare_route.py -v`
Expected: FAIL — `matched_by` key missing / registry assertions fail

- [ ] **Step 3: Implement** — rework `api/app/routes/compare.py`:

Add to imports:

```python
from app import pipeline, registry
from app.db import Database, get_db
from app.matching.llm_judge import JudgeBudget
from app.matching.structured import ProductRecord
from app.scrapers.bridge import fetch_product
```

Add fields to `CompetitorMatch`:

```python
    matched_by: str | None = None
    pack_note: str | None = None
```

Replace `_compare_one` with (keep `_best_match`, `_prefilter_candidates`, `_in_price_band`, `_empty_cell`, `_parse_dk_xlsx` as-is; delete the now-unused `_scrape_all_queries` — it moved to `pipeline.scrape_all_queries`):

```python
def _cell_to_match(cid: str, cname: str, cell: pipeline.Cell,
                   dk_price: float | None) -> CompetitorMatch:
    c = cell.candidate
    if c is None or cell.verdict is None:
        return _empty_cell(cid, cname, cell.candidates_seen)
    diff = round(dk_price - c.price, 2) if dk_price and dk_price > 0 else None
    return CompetitorMatch(
        competitor_id=cid, competitor_name=cname,
        candidates_seen=cell.candidates_seen,
        matched_name=c.name, matched_url=c.url, matched_price=c.price,
        matched_image=c.image, in_stock=c.in_stock,
        verdict=cell.verdict, score=cell.confidence, cosine=None,
        reasons=cell.reasons, price_diff_vs_dk=diff,
        matched_by=cell.matched_by, pack_note=cell.pack_note,
    )


async def _resolve_dk(row: DkRow) -> tuple[CompetitorMatch | None, ProductRecord | None]:
    """Search dentalkart.com, pick the best self-match, enrich via PDP."""
    dk_raw = await scrape_competitor("dentalkart", row.name)
    dk_match = _best_match(row.name, dk_raw, None)
    if dk_match is None:
        return None, None
    dk_match.competitor_id = "dentalkart"
    dk_match.competitor_name = "Dentalkart"
    pdp = await fetch_product("dentalkart", dk_match.matched_url or "")
    src = pdp or next(
        (c for c in dk_raw if c.url == dk_match.matched_url), None)
    if src is None:
        return dk_match, None
    return dk_match, pipeline.record_from(src)


async def _compare_one(
    row: DkRow, db: Database | None, budget: JudgeBudget
) -> CompareResult:
    dk_match, dk_record = await _resolve_dk(row)
    if dk_match is None or dk_record is None:
        # Not on dentalkart.com — report empty cells, don't guess.
        return CompareResult(
            dentalkart=row, dentalkart_match=None,
            competitors=[_empty_cell(cid, cname, 0) for cid, cname in COMPETITORS],
        )

    product_id: int | None = None
    if db is not None:
        try:
            product_id = await registry.upsert_product(db, dk_record)
        except Exception:  # noqa: BLE001 — registry is best-effort
            product_id = None

    ctx = ProductContext(
        description=dk_record.description or None,
        packaging=dk_record.packaging or None,
        sku=dk_record.sku,
    )
    queries = extract_smart_queries(dk_record.name, ctx) or [row.name]
    dk_price = dk_match.matched_price

    async def one_competitor(cid: str, cname: str) -> CompetitorMatch:
        # Phase 2: registry hit -> cheap refresh.
        if db is not None and product_id is not None:
            try:
                links = await registry.get_active_links(db, product_id, cid)
            except Exception:  # noqa: BLE001
                links = []
            if links:
                cell = await pipeline.refresh(cid, links[0])
                if cell is not None:
                    return _cell_to_match(cid, cname, cell, dk_price)
        # Phase 1: full discovery.
        cell = await pipeline.discover(
            cid, queries, dk_record,
            budget=budget, db=db, product_id=product_id,
        )
        return _cell_to_match(cid, cname, cell, dk_price)

    out = list(await asyncio.gather(
        *(one_competitor(cid, cname) for cid, cname in COMPETITORS)
    ))
    return CompareResult(dentalkart=row, dentalkart_match=dk_match, competitors=out)
```

Update the route handlers:

```python
@router.post("/single", response_model=CompareResult)
async def compare_single(row: DkRow) -> CompareResult:
    db: Database | None
    try:
        db = await get_db()
    except Exception:  # noqa: BLE001 — run stateless without a DB
        db = None
    try:
        budget = JudgeBudget(get_settings().llm_judge_budget_per_run)
        return await _compare_one(row, db, budget)
    finally:
        if db is not None:
            await db.close()
```

and rework `compare_batch`'s execution block (everything after `rows` is parsed) to share one DB handle and one judge budget across the whole upload:

```python
    db: Database | None
    try:
        db = await get_db()
    except Exception:  # noqa: BLE001 — run stateless without a DB
        db = None
    budget = JudgeBudget(get_settings().llm_judge_budget_per_run)
    sem = asyncio.Semaphore(max(1, concurrency))

    async def gated(r: DkRow) -> CompareResult:
        async with sem:
            return await _compare_one(r, db, budget)

    try:
        results = await asyncio.gather(*(gated(r) for r in rows))
    finally:
        if db is not None:
            await db.close()
    return CompareBatchResponse(total=len(results), results=results)
```

- [ ] **Step 4: Run the tests**

Run: `cd api && uv run pytest tests/routes/test_compare_route.py tests/routes -v`
Expected: new tests PASS; existing route tests stay green

- [ ] **Step 5: Full suite + lint**

Run: `cd api && uv run pytest -q && uv run ruff check . && uv run mypy app`
Expected: all green (parity fixture untouched)

- [ ] **Step 6: Commit**

```bash
git add api/app/routes/compare.py api/tests/routes/test_compare_route.py
git commit -m "feat(api): two-phase compare — registry refresh + PDP discovery + judge"
```

---

### Task 15: Feedback → registry promote/demote

**Files:**
- Modify: `api/app/routes/feedback.py`
- Modify: `api/app/static/index.html`
- Test: `api/tests/routes/test_feedback_route.py`

- [ ] **Step 1: Write the failing test** — append to `api/tests/routes/test_feedback_route.py`:

```python
def test_feedback_updates_link_status():
    import asyncio
    from app import registry
    from app.matching.structured import ProductRecord

    async def seed():
        db = await get_db()
        try:
            pid = await registry.upsert_product(db, ProductRecord(
                name="Wizdent Master Design Refill - A3B",
                url="https://www.dentalkart.com/wizdent.html", source="dentalkart"))
            await registry.upsert_link(
                db, pid, "pinkblue", "https://pinkblue.in/x",
                verdict="confirmed", confidence=0.9, matched_by="rules",
                reason="", llm_response=None)
            return pid
        finally:
            await db.close()
    pid = asyncio.run(seed())

    client = TestClient(app)
    res = client.post("/feedback", json=_payload(
        was_correct=False, dk_url="https://www.dentalkart.com/wizdent.html"))
    assert res.status_code == 200

    async def status():
        db = await get_db()
        try:
            row = await db.fetchrow(
                "SELECT status FROM product_links WHERE product_id = $1", pid)
            return row["status"]
        finally:
            await db.close()
    assert asyncio.run(status()) == "killed"
```

Also add `"dk_url": None,` to the `_payload` base dict, and a registry wipe to the `_clean_feedback` fixture (`DELETE FROM product_links` / `products` before `match_feedback`).

- [ ] **Step 2: Run to verify failure**

Run: `cd api && uv run pytest tests/routes/test_feedback_route.py -v`
Expected: new test FAILS (status stays `active`)

- [ ] **Step 3: Implement** — in `api/app/routes/feedback.py`:

Add to `FeedbackRequest`:

```python
    dk_url: str | None = None
```

In `post_feedback`, after the INSERT and before computing the total:

```python
        if req.dk_url and req.matched_url:
            from app.registry import set_link_status
            status = "human_verified" if req.was_correct else "killed"
            try:
                await set_link_status(
                    db, req.dk_url, req.competitor_id, req.matched_url, status)
            except Exception:  # noqa: BLE001 — feedback insert already landed
                pass
```

- [ ] **Step 4: UI — pass `dk_url`** — in `api/app/static/index.html`:

In `renderResults` (~line 933), capture the DK URL and thread it through:

```javascript
    const dkUrl = r.dentalkart_match?.matched_url ?? null;
```

and change both `cellHtml(...)` calls to pass it: `cellHtml(c, false, searchTerm, dkPrice, dkUrl)` / `cellHtml(r.dentalkart_match, true, searchTerm, dkPrice, dkUrl)`.

In `cellHtml` change the signature to `function cellHtml(c, isDk = false, searchTerm = "", dkPrice = null, dkUrl = null)` and add to `fbPayload`:

```javascript
    dk_url: dkUrl,
```

- [ ] **Step 5: Run tests**

Run: `cd api && uv run pytest tests/routes/test_feedback_route.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add api/app/routes/feedback.py api/app/static/index.html api/tests/routes/test_feedback_route.py
git commit -m "feat(api): 👍/👎 permanently promotes/kills registry links"
```

---

### Task 16: Golden set — endpoints, UI buttons, eval script

**Files:**
- Create: `api/app/routes/golden.py`
- Create: `api/scripts/eval.py`
- Modify: `api/app/main.py`
- Modify: `api/app/static/index.html`
- Test: `api/tests/routes/test_golden_route.py`

- [ ] **Step 1: Write the failing test** — create `api/tests/routes/test_golden_route.py`:

```python
import asyncio

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app


@pytest.fixture(autouse=True)
def _clean():
    async def _wipe():
        db = await get_db()
        try:
            await db.execute("DELETE FROM golden_links")
        finally:
            await db.close()
    asyncio.run(_wipe())
    yield
    asyncio.run(_wipe())


def test_golden_correct_and_no_match_roundtrip():
    client = TestClient(app)
    r1 = client.post("/golden", json={
        "dk_name": "GC Fuji IX A2", "source": "pinkblue",
        "competitor_url": "https://pinkblue.in/fuji", "label": "correct"})
    assert r1.status_code == 200
    r2 = client.post("/golden", json={
        "dk_name": "GC Fuji IX A2", "source": "oralkart",
        "competitor_url": None, "label": "no_match"})
    assert r2.status_code == 200
    assert client.get("/golden/count").json()["count"] == 2


def test_golden_upsert_replaces_same_pair():
    client = TestClient(app)
    for url in ("https://pinkblue.in/a", "https://pinkblue.in/b"):
        client.post("/golden", json={
            "dk_name": "X", "source": "pinkblue",
            "competitor_url": url, "label": "correct"})
    assert client.get("/golden/count").json()["count"] == 1


def test_golden_rejects_bad_label():
    client = TestClient(app)
    r = client.post("/golden", json={
        "dk_name": "X", "source": "pinkblue",
        "competitor_url": "https://x", "label": "maybe"})
    assert r.status_code == 422
```

- [ ] **Step 2: Run to verify failure**

Run: `cd api && uv run pytest tests/routes/test_golden_route.py -v`
Expected: FAIL with 404 on `/golden`

- [ ] **Step 3: Implement route** — create `api/app/routes/golden.py`:

```python
"""
Golden-set labeling. One row = a human-asserted truth: 'this Dentalkart
product's true link on <source> is <url>' or 'it has no match there'.
scripts/eval.py measures pipeline precision/recall against these rows.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.db import get_db

router = APIRouter(prefix="/golden", tags=["golden"])


class GoldenRequest(BaseModel):
    dk_name: str = Field(min_length=1)
    source: str = Field(min_length=1)
    competitor_url: str | None = None
    label: Literal["correct", "no_match"]


class GoldenResponse(BaseModel):
    status: Literal["ok"] = "ok"
    count: int


@router.post("", response_model=GoldenResponse)
async def post_golden(req: GoldenRequest) -> GoldenResponse:
    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO golden_links (dk_name, source, competitor_url, label)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (dk_name, source) DO UPDATE SET
              competitor_url = EXCLUDED.competitor_url,
              label = EXCLUDED.label, created_at = now()
            """,
            req.dk_name, req.source, req.competitor_url, req.label,
        )
        row = await db.fetchrow("SELECT count(*) AS c FROM golden_links")
        return GoldenResponse(count=int(row["c"]) if row else 0)
    finally:
        await db.close()


@router.get("/count")
async def golden_count() -> dict[str, int]:
    db = await get_db()
    try:
        row = await db.fetchrow("SELECT count(*) AS c FROM golden_links")
        return {"count": int(row["c"]) if row else 0}
    finally:
        await db.close()
```

In `api/app/main.py` add:

```python
from app.routes import golden as golden_route
```

and after the other `include_router` lines:

```python
app.include_router(golden_route.router)
```

- [ ] **Step 4: Run route tests**

Run: `cd api && uv run pytest tests/routes/test_golden_route.py -v`
Expected: 3 PASS

- [ ] **Step 5: UI buttons** — in `api/app/static/index.html`:

In `cellHtml`, inside the `fb-row` div (after the 👎 button), add a golden button (matched cells only):

```javascript
          <button class="fb-btn" title="Save as golden truth"
            onclick="sendGolden('${fbId}', 'correct')">⭐</button>
```

In the empty-cell branch at the top of `cellHtml`, replace the returned `<td>` with one that includes a tiny no-match golden button (skip when `isDk`):

```javascript
  if (!c || c.matched_price == null) {
    const gId = Math.random().toString(36).slice(2);
    const noMatchBtn = !isDk && c ? `
      <div class="fb-row" id="fb-${gId}" data-payload='${enc(JSON.stringify({
        search_term: searchTerm, competitor_id: c.competitor_id }))}'>
        <button class="fb-btn" title="Golden: no match exists here"
          onclick="sendGolden('${gId}', 'no_match')">∅</button>
      </div>` : "";
    return `<td><div class="empty-cell">no match<small>${c?.candidates_seen ?? 0} candidates scanned</small></div>${noMatchBtn}</td>`;
  }
```

After `sendFeedback`, add:

```javascript
async function sendGolden(fbId, label) {
  const row = document.getElementById("fb-" + fbId);
  if (!row) return;
  let payload;
  try { payload = JSON.parse(row.dataset.payload); } catch { return; }
  const body = {
    dk_name: payload.search_term,
    source: payload.competitor_id,
    competitor_url: label === "correct" ? (payload.matched_url ?? null) : null,
    label,
  };
  try {
    const r = await fetch("/golden", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const thanks = document.createElement("span");
    thanks.className = "fb-thanks";
    thanks.textContent = "golden ✓";
    row.appendChild(thanks);
  } catch (e) {
    logLine(`golden failed: ${e.message}`);
  }
}
window.sendGolden = sendGolden;
```

- [ ] **Step 6: Eval script** — create `api/scripts/eval.py`:

```python
"""
Pipeline evaluation against golden_links.

For every labeled (dk_name, source) pair, run the live compare pipeline and
check the predicted URL against the golden truth. Reports per-source and
overall precision/recall.

  correct + predicted same URL        -> true positive
  correct + predicted other/none      -> false negative (+FP if other URL)
  no_match + predicted none           -> true negative
  no_match + predicted any URL        -> false positive

Usage:  cd api && uv run python scripts/eval.py
Needs:  Postgres + the Node sidecar running (live scrapes).
"""
from __future__ import annotations

import asyncio
from collections import defaultdict

from app.db import get_db
from app.matching.llm_judge import JudgeBudget
from app.routes.compare import DkRow, _compare_one
from app.settings import get_settings


def _norm_url(u: str | None) -> str:
    return (u or "").split("?")[0].rstrip("/").lower()


async def main() -> None:
    db = await get_db()
    try:
        rows = await db.fetch(
            "SELECT dk_name, source, competitor_url, label FROM golden_links "
            "ORDER BY dk_name"
        )
        if not rows:
            print("golden_links is empty — label some rows in the UI first (⭐ / ∅).")
            return

        golden: dict[str, list] = defaultdict(list)
        for r in rows:
            golden[r["dk_name"]].append(r)

        stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        budget = JudgeBudget(get_settings().llm_judge_budget_per_run * 10)

        for i, (dk_name, labels) in enumerate(golden.items(), 1):
            print(f"[{i}/{len(golden)}] {dk_name}")
            result = await _compare_one(DkRow(name=dk_name), db, budget)
            predicted = {
                c.competitor_id: _norm_url(c.matched_url)
                for c in result.competitors
                if c.verdict in ("confirmed", "possible")
            }
            for g in labels:
                src, s = g["source"], stats[g["source"]]
                pred = predicted.get(src, "")
                truth = _norm_url(g["competitor_url"])
                if g["label"] == "correct":
                    if pred and pred == truth:
                        s["tp"] += 1
                    else:
                        s["fn"] += 1
                        if pred:
                            s["fp"] += 1
                else:  # no_match
                    if pred:
                        s["fp"] += 1
                    else:
                        s["tn"] += 1

        print("\n== results ==")
        totals: dict[str, int] = defaultdict(int)
        for src, s in sorted(stats.items()):
            for k, v in s.items():
                totals[k] += v
            p = s["tp"] / (s["tp"] + s["fp"]) if s["tp"] + s["fp"] else 0.0
            r = s["tp"] / (s["tp"] + s["fn"]) if s["tp"] + s["fn"] else 0.0
            print(f"{src:12s} tp={s['tp']:3d} fp={s['fp']:3d} fn={s['fn']:3d} "
                  f"tn={s['tn']:3d}  precision={p:.2f} recall={r:.2f}")
        p = totals["tp"] / (totals["tp"] + totals["fp"]) if totals["tp"] + totals["fp"] else 0.0
        r = totals["tp"] / (totals["tp"] + totals["fn"]) if totals["tp"] + totals["fn"] else 0.0
        print(f"{'OVERALL':12s} precision={p:.2f} recall={r:.2f}")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 7: Verify script imports + full suite**

Run: `cd api && uv run python -c "import scripts.eval" && uv run pytest -q && uv run ruff check . && uv run mypy app`
Expected: clean, all tests pass

- [ ] **Step 8: Commit**

```bash
git add api/app/routes/golden.py api/app/main.py api/app/static/index.html api/scripts/eval.py api/tests/routes/test_golden_route.py
git commit -m "feat(api): golden-set labeling (⭐/∅) + precision/recall eval script"
```

---

### Task 17: Docs + final verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README** — in the Architecture section, replace the matching-engine line with:

```
              │  matching engine: search → triage → PDP fetch → structured
              │  attribute match → LLM judge (borderline) → match registry
```

Add after the "Tests" section:

```markdown
## Match registry & golden set

First run for a product does full discovery (search + PDP fetch + structured
match + LLM judge for borderline pairs) and stores the verified link in
`product_links`. Later runs just re-scrape the stored URLs for fresh prices.
👍 permanently verifies a link, 👎 kills it. ⭐ saves a golden-truth label
(∅ on an empty cell = "no match exists"); measure accuracy with:

​```bash
cd api
uv run python scripts/eval.py
​```

Set `ANTHROPIC_API_KEY` in `.env` to enable the LLM judge (Claude Haiku,
budget-capped per run). Without it the pipeline runs rules-only and
borderline pairs stay POSSIBLE.
```

- [ ] **Step 2: Full verification**

Run: `cd api && uv run pytest -q && uv run ruff check . && uv run mypy app`
Run: `npx tsc --noEmit`
Expected: everything green

- [ ] **Step 3: End-to-end manual smoke**

Start both processes (`npm run scrape-server`, `cd api && uv run uvicorn app.main:app --port 8000`), open `http://localhost:8000`, run a single-product search (e.g. "GC Fuji IX GP"). Verify: cells show verdict badges, a second identical search is visibly faster and the cell reason starts with `registry`, and ⭐/👍/👎 all respond.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: two-phase matching, registry, golden set + eval"
```
