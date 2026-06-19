# Worklog — Product Comparison Tool

> **Purpose:** This file is the single source of truth for what has been done,
> the current state, and what's next. Update it at the end of every working
> session. Newest entries go at the top of the Log.

---

## Current State (as of 2026-06-19)

- **Repo recovered.** Cloned `dk-utkarsh/Product-comparison-` (private) into
  `~/Desktop/PriceComparison`. Local-only unpushed optimization work was lost
  and is being reconstructed.
- **Both servers run locally and the tool works end-to-end** (verified live).
- **Mode:** stateless (no Postgres) and **LLM judge OFF** (no `ANTHROPIC_API_KEY`).
- **Active competitors:** `pinkblue`, `oralkart`, `dentmark` (3 of 10 scrapers
  wired in via `COMPETITORS` in `api/app/scrapers/bridge.py`).

### How to run (two processes)

```bash
# Terminal 1 — Node scraper sidecar (port 3100)
cd ~/Desktop/PriceComparison
npm run scrape-server          # → http://127.0.0.1:3100/health

# Terminal 2 — FastAPI app + UI (port 8000)
cd ~/Desktop/PriceComparison/api
export PATH="$HOME/.local/bin:$PATH"   # uv lives here
uv run uvicorn app.main:app --port 8000   # → http://localhost:8000/
```

- UI: http://localhost:8000/  (drag-drop an xlsx with a `Product Name` column)
- Docs: http://localhost:8000/docs
- Single test: `POST /compare/single  {"name": "..."}`
- Batch: `POST /compare/batch` (xlsx upload)

### Environment notes

- **Python must be 3.12** (`>=3.12,<3.13`); system had 3.14. Installed `uv`
  (`~/.local/bin`), which manages the 3.12 toolchain. `uv sync` in `api/`.
- `npm install` at repo root for the TS scrapers.
- `api/.env` created for stateless dev: `DATABASE_URL=postgresql://localhost:1/nodb`
  (closed port → asyncpg fails fast → graceful stateless fallback),
  `ANTHROPIC_API_KEY=` (empty → judge off).
- First `/compare` call downloads the embedding model (~80MB, all-MiniLM-L6-v2).

---

## Architecture (one-liner)

`Browser → FastAPI (:8000, Python: matching + UI + registry) → Node sidecar
(:3100, TS scrapers) → competitor sites.`

**Matching funnel** (cheap→expensive, strict→stricter):
1. Resolve product on Dentalkart (anchor record).
2. Build 4–8 progressive search queries (`query_builder.py`).
3. Scrape competitors in parallel, pool unique candidates by URL.
4. Prefilter → **triage** (gates + weighted score, keep top-K) →
   **structured_match** (CONFIRMED/BORDERLINE/REJECTED) →
   **LLM judge** (borderline only) → best cell + price Δ.
5. Registry (Postgres): first run discovers, later runs cheap-refresh;
   👍 verify / 👎 kill / ⭐ golden label.

**Key files**
- Orchestration: `api/app/routes/compare.py`, `api/app/pipeline.py`
- Matching: `api/app/matching/{triage,structured,gates,score,query_builder,llm_judge}.py`
- Tuning knobs: `api/app/settings.py` (thresholds + score weights)
- Scrapers: `lib/scrapers/*.ts`, served by `api/bridges/scrape-server.ts`
- Registry/DB: `api/app/registry.py`, `api/app/db.py`, `api/migrations/`

---

## Known Issues / Observations (candidates for optimization)

- [ ] **No accuracy baseline yet.** Need to run `test-20-products.xlsx` and/or
      the golden-set eval (`api/scripts/eval.py`) to measure precision/recall
      before tuning anything.
- [ ] **7 scrapers unused.** dentaid, medikabazar, metroorthodontics,
      shop4smile, smilestream, surgicalmart exist but aren't in `COMPETITORS`.
- [ ] **`.env.example` is stale** vs `settings.py` (e.g. `POSSIBLE_THRESHOLD`
      0.55 vs 0.62; missing `SCORE_W_TOKEN`/`SCORE_W_FUZZ`).
- [ ] **LLM judge disabled** — borderline pairs fall back to "possible".
- [ ] **Stateless** — registry/feedback (refresh, 👍/👎/⭐) inactive until
      Postgres is wired up.

---

## Goal

Reconstruct + improve match **accuracy and precision** (the lost local work).
Highest-leverage areas: score weights + thresholds (`score.py`/`settings.py`),
gates + structured rules (`gates.py`/`structured.py`), and the LLM judge for the
ambiguous middle.

---

## Log (newest first)

### 2026-06-19 — Julldent surgical box SOLVED + out-of-stock + next: knives

**DONE — config sub-variant resolution + DK child prices via RSC.**
All 4 Julldent Korean Surgical Box variants now resolve to their exact child
with the correct price + full child name shown:
- Basic Plus+Torque ₹15995 · Premium+Torque ₹17995 · Basic+NonTorque ₹13995 ·
  Set of 4 Drills ₹8995 (was: every variant → parent ₹1995).
- Baseline regression check: 0 verdicts lost (Phyx ₹793 & Koden ₹603 now
  *closer* to DK; GDC scissors gained a match). GC still correct.

What changed (on top of the GC sub-variant work):
- `lib/variant-spec.ts` + `variant_spec.py`: added `kitTier`
  (only/set/basic/basic-plus/premium/standard/deluxe) + `torque`
  (torque/non-torque); config mismatch ⇒ never-match; categorical match ⇒ exact.
- `lib/scrapers/dentalkart.ts`: `parseGroupedChildren()` reads DK's Next.js RSC
  flight payload → per-child {name, sku, selling price, mrp, inStock,
  variantSpec}; filtered to real siblings by core-token overlap.
- `variant_spec.py`: `config_from_text()` (Python mirror) + `base_name()`
  (strip config words to find the grouped parent).
- `routes/compare.py` `_resolve_dk`: also searches the base name so the grouped
  parent surfaces; `_pick_dk_child()` resolves the input name → exact child
  (config-compatible + closest name) and shows its full name + real price +
  stock.
- Out-of-stock: matcher never filters by stock; correct config wins regardless
  of stock; OOS flagged in reasons; Δ shown when priced.

**Periodontal Knives — SOLVED.** All 9 children of "Julldent Periodontal Knives
(JULL-DENT 191)" now resolve to their exact sub-variant (all ₹995): Buck KB 3/4,
Buck KB 5/6, Crane Kaplan KCK 3, Goldman Fox KGF 8/9/11, Orban KO 1/2 &
KO 12K P03A, Kirkland KK 15/16. Two fixes:
- **Child filter was too strict** (`parseGroupedChildren`): knife children share
  only {julldent, periodontal} with the parent (plural "Knives" vs "Knife", and
  each child carries its OWN code "(KGF 8)" not "(JULL-DENT 191)"). Changed the
  filter to **brand + ≥1 shared core token** (still excludes recommended items).
- **Model-code matching** (`_pick_dk_child` + `_paren_code`): exact last-paren
  code (KGF 8 vs KGF 9 vs KO 1/2) decides the child when children share a config;
  only applied when codes actually differ across children (so it doesn't disturb
  the surgical box, whose children all share "JULL-DENT 223").

Regression check (GC, surgical box, baseline): GC ₹2450/₹2580 and surgical box
₹15995 unchanged. Baseline diffs are live competitor-search flicker on borderline
"possible" cells (Periotome/Shofu/Scissors — no config/codes, so DK-side changes
can't affect them) plus the Phyx ₹793 / Koden ₹603 price improvements. No real
regressions.

### 2026-06-19 — Julldent surgical box (config sub-variants) + out-of-stock req

**Case:** "Julldent Basic Plus Korean Surgical Box With Torque Ratchet
(JULL-DENT 223)". DK grouped product "Julldent Korean Surgical Box
(JULL-DENT 223)" has 5 children differing by CONFIGURATION (not grams):
Box Only / Set of 4 DLC Drills / Basic+NonTorque / Basic Plus+Torque /
Premium+Torque. All share base SKU. Tool collapses every variant to the
parent (₹1995) → wrong price/identity for the specific variant searched.

**Findings (recon):**
- Competitors (pinkblue/oralkart) DON'T stock Julldent at all (0 hits);
  dentmark returns unrelated keyword junk. So this product has NO valid
  competitor comparison — only the DK self-match matters here.
- Two gaps to fix DK side:
  1. **Config-descriptor variant matching** — the current variant_spec only
     knows grams/Extra/tier/pieces. Need to capture tier (Only/Set-of-N/
     Basic/Basic Plus/Premium) + ratchet (Torque vs Non-Torque) so the right
     child is identified and siblings never cross-match.
  2. **DK per-child prices** — BLOCKER: prices live in the Next.js RSC flight
     payload behind multi-level escaped refs (`pricing:"$83"` → row 83 → …).
     No JSON-LD offers. Children aren't independently searchable by SKU/name
     (search returns the parent ₹1995). Extraction is fragile.

**Out-of-stock requirement (user):** show the correct product even when not in
stock. Current state: the Python matcher does NOT filter by stock (3 OOS cells
matched in baseline). The only place OOS can be lost is the `price > 0` filter
(OOS items sometimes have no price) + scrapers that skip OOS cards. Design
choice needed: keep price-less OOS items (show as OOS, Δ N/A) vs require price.

Status: paused for direction on (a) how hard to push on DK child-price
extraction, (b) out-of-stock display behavior.

### 2026-06-19 — Sub-variant matching IMPLEMENTED + verified (GC Gold Label 9)

Shipped the 3-layer sub-variant fix end to end. **GC Gold Label 9 now correct:**
- DK truth ₹2760 (15g+13.1g) · Pinkblue ₹2450 confirmed/**exact** (Δ +310) ·
  Oralkart ₹2580 confirmed/same-tier (Δ +180). Extra line excluded.
- Was: bogus −₹1416/−₹1474 against the 5g mini sub-variant.
- **Baseline regression check: 0 verdict changes** (19 confirmed / 5 possible /
  36 none, identical). Only Koden bracket pinkblue ₹574→₹603 (same verdict,
  closer to DK ₹675).

What changed:
- `lib/variant-spec.ts` — composition parser (powder g, liquid g/ml, capsules,
  pieces, Extra line, big/mini tier) + nearest-number assignment.
- `lib/scrapers/dentalkart.ts` — attach source-of-truth `variantSpec` (first
  non-Extra child from `child_names`).
- `lib/scrapers/oralkart.ts` — per-variant `variantSpec` from Shopify `.js`.
- `lib/scrapers/pinkblue.ts` — parse grouped-product variant TABLE (name +
  package content + per-variant price); pack size from variant NAME only.
- `lib/pack-detector.ts` — `x N` no longer matches "1 x 15 g" / "x 13.1 g"
  (size/decimal, not a pack) — this bug had wrecked the price band.
- `api/app/matching/variant_spec.py` — Python spec model + compare
  (exact / same-tier / different-size / different-formulation / unknown).
- `api/app/scrapers/bridge.py` — carry `variant_spec` + `variants` through.
- `api/app/matching/structured.py` — reject Extra↔non-Extra; record spec_match;
  size diff → pack_note; exact spec → data_ok.
- `api/app/pipeline.py` — `select_variant()`: choose competitor sub-variant by
  spec match then price-proximity to DK; guards (≥2 real variants, needs a size
  signal, skip "Default Title", no name rename).
- `api/app/routes/compare.py` — DK anchor uses truth spec + listing price;
  pass `dk_price`; per-unit Δ via composition base quantity; expose `spec_match`.
- Tests: 108 pass (failures are DB/pgvector infra only, need Postgres).

Still open / next: per-unit Δ path (DK-size unavailable at competitor) not yet
seen live; revisit hard cases from baseline (Tor Vm crowns 64 vs 40 pcs, gel
pack-of-6 vs single, archwire→implant-model false match, Gracy vs Universal
curette). Consider enabling the LLM judge + Postgres registry.

### 2026-06-19 — Sub-variant problem identified (GC Gold Label 9)

**User requirement (NEW, high priority):** the tool must match **sub-variants**
(same base product, different size/composition) and use the **Dentalkart product
as the source of truth** for which sub-variant is correct. Before showing a
result, the competitor's exact spec must match DK's exact spec.

**Case:** "GC Gold Label 9 Posterior Restorative GIC".
- DK source of truth = **15g powder + 13.1g (10.5mL) liquid**, ₹2,760.
- Tool wrongly matched the **5g/mini** sub-variant on Pinkblue (₹1,344) and
  Oralkart (₹1,286) → showed a bogus ~₹1,400 "saving". `unit_price_ratio≈0.98`
  was the tell (per-unit nearly equal → size mismatch).

**Root cause (confirmed via raw-source recon):** no scraper extracts variant /
composition data. The specs exist upstream but we discard them:
- **DK search API** = configurable product with `child_products` / `child_names`
  + `specifications` / `full_description` / `packaging_contents`. Parent default
  ₹2,760; PDP scraper grabbed a child (₹1,369). DK has size children.
- **Oralkart (Shopify)** `/products/{handle}.js` exposes 4 variants
  (Big Pack ₹2,580 / Mini Pack ₹1,286 / Extra variants). Scraper took only the
  default (Mini ₹1,286).
- **Pinkblue** PDP HTML lists compositions as grouped options:
  `5g+3g(2.4ml)`, `15g+13.1g(10.5mL)` (=DK), `15g+8g(6.4ml)`. Scraper ignored them.

**Fix plan (3 layers):**
1. **Scrapers** capture all variants + per-variant composition & price
   (DK children, Shopify variant JSON, Pinkblue grouped options). Shared
   composition parser in `lib/` → `{powder_g, liquid_g, liquid_ml, capsules, pack}`.
2. **DK = source of truth:** anchor carries its exact composition; for each
   competitor pick the variant whose composition matches DK's.
3. **Matching/verdict** (`structured.py`): if compositions differ → different
   sub-variant → don't CONFIRM at that price; only show Δ when specs match.

Decision (user): when no exact-size match, show the **closest** variant and
normalize to **per-unit** price so the comparison stays accurate (e.g. DK
pack-of-6 vs competitor single → compare per piece). Never cross Extra↔non-Extra.

**Refined design (after recon):**
- DK is a Magento *grouped* product; per-child prices aren't independently
  queryable. So don't chase DK's price→child mapping. Instead:
- **Scrapers emit each sub-variant as its own candidate** (own spec + price +
  url): Oralkart from Shopify `.js` variants, Pinkblue from PDP grouped options,
  DK children from `packaging_contents`/`child_names`.
- Each candidate carries a parsed `variantSpec` (lib/variant-spec.ts) through the
  bridge JSON into Python.
- **Selection (Python):** pick the competitor variant by (1) composition spec
  match to DK truth (exact > same-tier; never Extra↔non-Extra), then
  (2) **price-proximity to DK's listing price** as tiebreaker/fallback — this
  handles grams-less labels (DK ₹2760 ≈ Oralkart Big ₹2580, far from Mini ₹1286).
- **DK truth spec** = first non-Extra child (primary listed child = 15g+13.1g),
  and DK anchor price = the listing price (₹2760), not the cheaper PDP child.
- Status: `lib/variant-spec.ts` built + unit-tested. ScraperAPI key wired into
  root `.env` (5000 credits, only proxies pinkblue; sidecar stays direct in dev).

### 2026-06-19 — Baseline run (test-20-products.xlsx)

Saved `results/baseline-2026-06-19.json`. 20 rows, DK resolved 19/20. Of 60
competitor cells: 19 confirmed, 5 possible, 36 no-match. Notable suspicious
results to revisit: row 4 (Tor Vm 64pcs vs 40pcs — pack mismatch), row 8 (Gel
pack-of-6 vs single ₹167), row 13 (Ortho archwire matched to an implant model),
row 18 (Gracy curette matched to a Universal curette). Same sub-variant/size
theme recurs.

### 2026-06-19 — Recovery, setup, and orientation
- Token-authenticated to GitHub (account `dk-utkarsh`), identified and cloned
  the private repo `Product-comparison-` into the empty project folder. Stripped
  the token from the git remote afterward.
- Added a project-context section to `CLAUDE.md`; committed + pushed (`65b163a`).
- Installed `uv`; `uv sync` (Python 3.12 + ML deps); `npm install`.
- Created `api/.env` for stateless local dev.
- Started both servers; verified health on :3100 and :8000.
- Ran `/compare/single` for "Dentsply Maillefer ProTaper Gold Rotary Files" —
  confirmed matches on DK (₹3629), Pinkblue (₹2748, −₹881), Oralkart
  (₹3109, −₹520). Full pipeline works.
- Read the whole codebase; documented the search + matching flow.
- Created this worklog.

**Next:** establish an accuracy baseline (run `test-20-products.xlsx`), then
decide the first optimization target.
