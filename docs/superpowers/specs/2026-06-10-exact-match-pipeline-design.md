# Exact-Product Matching Pipeline (Sub-project 1 of the Price Intelligence Platform)

**Status:** Approved by user (design dialogue 2026-06-10)
**Author:** automation1@dentalkart.com
**Date:** 2026-06-10

## 1. Goal

Make product matching trustworthy: given a Dentalkart product, find the *exact same
product* (same variant) on pinkblue.in, oralkart.com, and dentmark.com, and compare
prices on a normalized per-unit basis. Today matching runs on product names from
competitor search results alone, and names vary too much across sites. This design
adds description / key-features / packaging data from product detail pages (PDPs)
on both sides, a structured attribute matcher, an LLM judge for borderline pairs,
and a persistent match registry so each product is matched once and then only
re-priced.

This is sub-project 1 of the larger Dental Product Price Intelligence Platform.
Later sub-projects (not designed here): scheduled monitoring + price history,
dashboards/alerts/reports, more competitors.

## 2. Decisions locked during brainstorming

| Question | Decision |
| --- | --- |
| First focus | Matching accuracy only; monitoring/dashboards later |
| Dentalkart catalog data | No internal export — scrape dentalkart.com PDPs |
| Competitor rich data | Fetch PDPs for top 3–5 candidates per competitor, cache them |
| Ground truth | Hand-labeled golden set (~100–200 products incl. "no match exists") + accumulated 👍/👎 feedback |
| "Exact product" means | Same product line AND same variant attributes; pack size may differ → normalize to unit price; different variant = VARIANT, not a price match |
| Persistence | Persistent match registry (`product_links`); feedback permanently promotes/demotes links |
| Competitors in scope | pinkblue, oralkart, dentmark (design stays scraper-agnostic) |
| Matching approach | A + C hybrid: structured attribute matching backbone + LLM judge for the borderline band only |

Approaches considered and rejected: pure embedding upgrade (bi-encoder +
cross-encoder) — cross-encoders cannot reliably distinguish variant attributes
(A2 vs A3, .016 vs .018), which is exactly our failure mode.

## 3. Architecture: two-phase

### Phase 1 — Link discovery (expensive, once per product)

1. **Resolve Dentalkart product.** Scrape the dentalkart.com PDP for the xlsx
   row / search term: name, description, key features, packaging, variants,
   SKU, price, MRP. Cache in `products`. If no PDP is found, report the row as
   "not on dentalkart.com" — do not guess.
2. **Progressive search per competitor** (existing `query_builder`) → candidate
   list (name, price, url).
3. **Cheap triage on names** (existing gates + embeddings + rapidfuzz) → keep
   top 3–5 candidates per competitor, reject obvious junk.
4. **Fetch candidate PDPs** via new `fetchProduct(url)` per scraper → rich
   record per candidate, cached in `competitor_products`.
5. **Structured match** (Approach A): attribute extraction on both sides →
   field-wise compare → CONFIRMED / BORDERLINE / REJECTED with reasons.
6. **LLM judge** (Approach C) for BORDERLINE only: Claude Haiku, structured
   verdict + reason, result cached forever in the registry.
7. **Write to `product_links`** registry: dk_product ↔ competitor_url, verdict,
   confidence, reason, matched_by.

### Phase 2 — Price refresh (cheap, every subsequent run)

Registry hit → re-fetch only the known PDP URLs → fresh prices. Registry miss →
run Phase 1. Prices are never trusted from the registry; always re-scraped.

Feedback loop: 👍 in the UI promotes a link to `human_verified` (never
re-judged); 👎 sets `killed` and triggers re-discovery excluding that URL.
The upload UI flow is unchanged; uploads get faster and more accurate as the
registry fills.

## 4. Components

### TypeScript (lib/ + sidecar)

- `fetchProduct(url)` added to dentalkart, pinkblue, oralkart, dentmark
  scrapers. Parses a PDP into the existing `ProductData` shape, reliably
  filling `description`, `packaging`, `variants`, `sku`. Pinkblue keeps
  routing through ScraperAPI when `SCRAPER_API_KEY` is set.
- Sidecar route: `POST /product { scraper, url }` alongside the existing
  search route.

### Python (api/app/)

- `matching/attributes.py` — **extended, not replaced.** Extraction runs over
  name + description + packaging (today: name only). New attributes that live
  mostly in descriptions: material, dimensions (e.g. `0.016"`), wire form
  (upper/lower), curing type. Existing regexes stay.
- `matching/structured.py` — new. Field-wise comparison of two extracted
  records → feature vector + deterministic verdict (CONFIRMED / BORDERLINE /
  REJECTED) with human-readable reasons ("shade mismatch: A2 vs A3").
- `matching/llm_judge.py` — new. Claude Haiku with strict-JSON structured
  output, called only for BORDERLINE. Budget cap per run (default ~30 calls,
  configurable). Graceful degradation: over-budget / API error / no key →
  verdict stays POSSIBLE with reason "needs review" — never silently confirmed.
- `registry.py` — new. Read/write `product_links`; promote/demote logic wired
  to the existing feedback route.
- `routes/compare.py` — reworked to the two-phase flow; response shape to the
  UI stays compatible.

## 5. Data model

Three new tables; `dentalkart_catalog` (sparse pre-filter index) and
`match_feedback` are kept.

```sql
products             -- scraped Dentalkart PDPs (our side)
  id, sku, url UNIQUE, name, description, packaging, key_features,
  brand, price, mrp, variants jsonb, attrs jsonb,   -- extracted attributes
  embedding vector(384),                            -- name+desc embedding
  scraped_at

competitor_products  -- scraped competitor PDPs (their side); the PDP cache
  id, source, url UNIQUE, name, description, packaging,
  price, mrp, in_stock, variants jsonb, attrs jsonb, scraped_at

product_links        -- the match registry
  id, product_id → products, source, competitor_url,
  verdict,             -- confirmed | possible | variant | rejected
                       -- (possible = unresolved borderline: thin data,
                       --  LLM budget exhausted, or LLM unavailable)
  confidence float,
  matched_by,          -- rules | llm | human
  reason text,
  llm_response jsonb,  -- raw judge output, for audit/training
  status,              -- active | human_verified | killed
  UNIQUE(product_id, source, competitor_url),
  created_at, updated_at
```

`match_feedback` handler additionally updates `product_links.status`.

## 6. Matching logic

Feature vector per candidate pair: brand agreement, product-line embedding
cosine, token overlap, fuzz ratio, per-attribute agreement (shade, ISO size,
taper, slot, concentration, viscosity, material, dimensions), pack ratio,
unit-price ratio.

Deterministic verdict rules:

- **REJECTED:** any hard gate fails — incompatible category, brand conflict,
  or any variant attribute explicitly present on *both* sides and different
  (A2 vs A3, .016 vs .018).
- **CONFIRMED:** brand + product line strongly agree, every *shared* variant
  attribute equal, unit price within band.
- **BORDERLINE → LLM judge:** everything else — attributes missing on one
  side, names diverge but descriptions agree, ambiguous pack interpretation.

Pack handling: differing pack sizes never reject. Normalize to unit price,
carry a `pack_note` ("50/pack vs 10/pack") to the UI. The existing 5x price
band applies to **unit** price.

LLM judge contract: input = both sides' name + description + packaging +
extracted attrs. Output = `{same_product, same_variant, differences[],
confidence, reason}`. Mapping: `same_product && same_variant` → CONFIRMED;
`same_product && !same_variant` → VARIANT; else REJECTED. Each pair is judged
once ever (cached in `product_links.llm_response`).

## 7. Error handling

- **PDP fetch fails:** keep search-result data, match on name only, mark the
  link `thin_data: true`, cap confidence at POSSIBLE.
- **Competitor PDP HTML changes:** `fetchProduct` returns a partial record →
  same thin-data path; parse failures logged with URL.
- **Dentalkart PDP not found:** row reported "not on dentalkart.com".
- **LLM unavailable:** pipeline runs as pure Approach A; borderline stays
  POSSIBLE.

## 8. Testing & evaluation

- Unit tests (existing style, pytest): new extraction fields, `structured.py`
  verdict table, pack normalization, judge JSON parsing with mocked API.
- Golden set: labeling helper page (reusing the test UI) to record ~100–200
  true links including explicit "no match exists" rows; stored as a fixture.
- `scripts/eval.py`: runs the pipeline against the golden set, prints
  precision/recall per verdict and a confusion comparison vs the old matcher.
- The 20-case TS-parity fixture stays green.
- Follow-up (not this sub-project): train a light classifier (logistic
  regression / LightGBM) on accumulated labels to replace hand-set
  CONFIRMED/BORDERLINE thresholds and shrink the LLM borderline band.

## 9. Non-goals

- Scheduled monitoring, price history, dashboards, alerts (later sub-projects).
- Reactivating the other 7 scrapers in `lib/scrapers/`.
- Training the ML re-ranker now (needs accumulated labels first).
- Any UI redesign beyond the labeling helper and pack/reason annotations.
