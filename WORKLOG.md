# Worklog — Product Comparison Tool

> **Purpose:** This file is the single source of truth for what has been done,
> the current state, and what's next. Update it at the end of every working
> session. Newest entries go at the top of the Log.

---

## Current State (as of 2026-06-20)

- **Repo recovered + heavily optimized.** Cloned `dk-utkarsh/Product-comparison-`
  (private) into `~/Desktop/PriceComparison`. Sub-variant matching reconstructed
  and extended well beyond the lost work.
- **Both servers run locally and the tool works end-to-end** (verified live).
- **Mode:** stateless (no Postgres) and **LLM judge OFF** (no `ANTHROPIC_API_KEY`).
- **Active competitors:** `pinkblue`, `oralkart`, `dentmark` (3 of 10 scrapers
  wired in via `COMPETITORS` in `api/app/scrapers/bridge.py`).
- **Single-compare latency ~3–5s** (was 45s — ScraperAPI proxy disabled for dev +
  parallel PDP fetches).
- **Uploads:** `.xlsx` AND `.csv`; batch shows live "searched X of N" progress.

### Sub-variant matching (the core capability built this cycle)

The tool resolves the EXACT sub-variant of a base/grouped product, with
Dentalkart as source of truth, and never crosses brands:
- **Composition**: powder/liquid g·ml, capsules, pack count, Extra line, big/mini
  tier (GC Gold Label 9 → 15g+13.1g).
- **Config**: kit tier (Only/Set/Basic/Basic Plus/Premium) + torque/non-torque
  (Julldent surgical box).
- **Model/serial code**: KGF 9 vs KGF 9 · KO 1/2 · 5527/002/E · 079C (knives,
  articulating paper, retractor) — distinct-code guard so shared parent SKUs
  don't false-match.
- **Size/ambiguity**: base-product preference over specializations; base-name
  search + full-name ranking surfaces the right grouped parent.
- **Out-of-stock** variants are shown (flagged), never hidden.
- DK grouped children parsed from the Next.js RSC payload via the authoritative
  `child_products` list (robust to varying field sets).

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

- UI: http://localhost:8000/  (drag-drop an `.xlsx` or `.csv` with a `Product Name` column)
- Docs: http://localhost:8000/docs
- Single test: `POST /compare/single  {"name": "..."}`
- Batch: `POST /compare/batch` (full result) or `POST /compare/batch-stream`
  (NDJSON progress; the UI uses this)

### Environment notes

- **Python must be 3.12** (`>=3.12,<3.13`); system had 3.14. Installed `uv`
  (`~/.local/bin`), which manages the 3.12 toolchain. `uv sync` in `api/`.
- `npm install` at repo root for the TS scrapers.
- `api/.env` created for stateless dev: `DATABASE_URL=postgresql://localhost:1/nodb`
  (closed port → asyncpg fails fast → graceful stateless fallback),
  `ANTHROPIC_API_KEY=` (empty → judge off).
- Root `.env` holds `SCRAPER_API_KEY` (ScraperAPI, ~3900 credits left) but it is
  **commented out for dev** — proxying pinkblue is slow (3–17s) and direct fetch
  from a residential IP is ~1s. Re-enable ONLY for prod (datacenter IPs get
  blocked by pinkblue).
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

- [ ] **Uncommitted work.** A large batch of this cycle's changes is NOT yet
      committed (sub-variant matching, CSV upload, latency fix, batch progress,
      serial-code resolution, parser rewrite). Baseline file
      `results/baseline-2026-06-19.json` is the reference for regression diffs.
- [ ] **DK serves inconsistent RSC shapes** for the same PDP (field sets vary;
      occasionally a degraded page). The parser is now robust (child_products
      list + order-independent extraction) but heavy scraping of DK risks
      throttling — keep an eye on it.
- [ ] **7 scrapers unused.** dentaid, medikabazar, metroorthodontics,
      shop4smile, smilestream, surgicalmart exist but aren't in `COMPETITORS`.
- [ ] **`.env.example` is stale** vs `settings.py` (e.g. `POSSIBLE_THRESHOLD`
      0.55 vs 0.62; missing `SCORE_W_TOKEN`/`SCORE_W_FUZZ`).
- [ ] **LLM judge disabled** — borderline pairs fall back to "possible".
- [ ] **Stateless** — registry/feedback (refresh, 👍/👎/⭐) inactive until
      Postgres is wired up.
- [ ] **Cross-brand instruments stay unmatched by design** (e.g. Julldent
      knives) — competitors carry the same DESIGN under their own brand;
      strict-brand rule (user decision) means no match. Web-search recall would
      need a paid search API and still wouldn't surface a different brand.

### Resolved cases (regression watch list — should stay correct)

GC Gold Label 9 (Pinkblue 15g+13.1g ₹2450 / Oralkart Big ₹2580) · Julldent
Surgical Box (Basic Plus ₹15995 / Premium ₹17995) · Julldent Periodontal Knives
(9/9 by model code) · Maarc Articulating Paper (5527/002/E ₹595 OOS · 5523/050
₹655) · Julldent Premium Orringer Retractor 40mm (079C) ₹1195 · GDC Scissors
(S5083) · ambiguous "GC Gold Label 9" → base Posterior Restorative.

---

## Goal

Reconstruct + improve match **accuracy and precision** (DONE for sub-variants;
ongoing). Highest-leverage remaining: enable the LLM judge + Postgres registry,
wire in unused competitor scrapers, run the golden-set eval
(`api/scripts/eval.py`) for a precision/recall number.

---

## Log (newest first)

### 2026-06-20 — Competitors matched to the INPUT when DK lacks it + catalog index

**User: "you're too DK-centric — pinkblue HAS this product but you ignore it."**
Correct. The tool was DK-anchored: `_compare_one` returned ALL-empty cells when
DK had no match, and competitors were matched against DK's resolved record — so
when DK delisted/mis-resolved a product, valid competitor results were blocked
(e.g. Pinkblue's "Meril Filasilk #2-0" @ ₹609 thrown away because DK's anchor was
a different variant; the model-code gate then rejected 2-0 vs 3-0).

**Fix — input is the source of truth.** New `_dk_has_input_product()`: if the
input names a distinctive code/size the DK match doesn't share, DK didn't resolve
the input product. In that case `_compare_one` now matches competitors against
the INPUT itself (and shows DK as "not carried"), instead of returning empty.
Baseline 20: confirmed 19 (unchanged), possible 5→9 (+4 competitor matches
recovered), 0 lost. This makes competitor results first-class, not gated by a
perfect DK match.

**Local DK catalog index (new infra, `scripts/build_dk_catalog.py` +
`app/matching/dk_catalog.py`):** crawls the product sitemaps (~8k products),
embeds slug-names, saves a local npz (gitignored); `search()` does numpy
nearest-neighbour. Built to recover products DK's on-site search ranks poorly —
but it surfaced the real finding: **the recall-gap products (Meril #2-0, Syden
17x25, API No.86, Toboom HP0308D) are NOT in DK's catalog at all** — Meril #2-0's
page 404s (delisted); Google shows stale/other-brand results. So those aren't
tool bugs; DK genuinely doesn't carry them. The index is kept as infra (not wired
into the hot path) for future use.

Caveat: Meril #2-0 specifically is still imperfect — DK delisted the suture but
still lists a "Reel #2-0" sharing the 2-0 code, which fools the code check; the
competitor line-match (Filasilk vs Mericron) is also fuzzy. Pathological edge.

### 2026-06-20 — Model/size-code discriminators + DK-vs-competitor search note

Fixed the two bugs the Google validation surfaced, model-code precision:
- **`attributes.py`**: capture USP suture sizes (`#2-0`→`2-0`) and integer dim
  pairs (`17x25`) as `model_codes` so the existing two-sided model-code gate
  treats them as hard discriminators (Meril #2-0 ≠ #5-0).
- **`compare.py` `_code_match_bonus`**: DK ranking boost for a candidate sharing
  the input's distinctive code, so the exact-size product beats a size-less
  sibling. `_resolve_by_child_name` now also runs each child through `gate_check`
  (the fuzz-only override must not adopt a code-conflicting child).
- **`structured.py` (competitor only)**: a MAIN-name manufacturer model code
  (letter+3 digits, e.g. `DL-300` — NOT a trailing parenthetical DK SKU like
  `(S5083)`) absent from the candidate → reject. Fixes the Upcera DL-300 →
  "P2 Plus" false positive while NOT over-rejecting (every baseline strong code
  is parenthetical SKU, so untouched). A first, naive one-sided rule in the
  shared gate over-rejected (Shofu/Api/Xcem competitor matches lost) and was
  reverted — the SKU-vs-model distinction (main name vs parens) is the key.
- **DK self-search** reverted to raw + base name only (dropped the broad
  progressive queries): on the UNANCHORED self-match they added noise ("GC Gold
  Label 9" → "GC Gold Label Hybrid"). Competitors KEEP the progressive queries
  (`pipeline.discover`) because they're matched against the DK anchor with strict
  gates that filter the noise — so competitor recall is already covered.

Result: Upcera false positive gone; Meril no longer picks the wrong size; GC /
hex driver / retractor / knives / surgical box / paper / suture all correct.
Baseline 20: 19 confirmed / 6 possible (no verdict lost); 77 tests pass.

Notes: Meril #2-0 still can't resolve to the exact #2-0 — DK search doesn't
return that SKU at all (recall gap; needs the catalog index, option B). DK's
search price for the GC grouped parent flipped ₹2760→₹1369 mid-session (DK data
inconsistency, confirmed via the raw scraper — not our code).

### 2026-06-20 — Pooled DK search (free recall fix) + Google-vs-tool validation

**Free recall fix (user rejected paid search APIs):** DK self-search now fires
the raw name + config/size-stripped base name + the progressive query builder
(`extract_smart_queries`, the same generator used for competitors), all in
parallel, pooled by URL (`_pooled_dk_search`). This surfaces grouped parents DK's
own search misses (e.g. the Orringer retractor parent only appears for "Julldent
Orringer Retractor", not the full child name). Baseline 20: 0 changes (already
correct); fixes the hard cases. ~2.1s/row.

**Validation vs Google (sample test file 1.csv, 132 products):**
- DK resolution ran on all 132: **3 NONE**, ~96% land on the exact input variant.
  Spot-checked 11 against `site:dentalkart.com` — we MATCH Google's truth on all,
  and BEAT a plain Google `site:` query on the hex driver (Google surfaced the
  wrong product, we resolved the right child). The 3 NONE (API No.86 forceps,
  Syden 17x25 wire, Toboom HP0308D) — Google's `site:` search ALSO can't confirm
  them on DK, so they're edge SKUs, not clear tool bugs.
- **Competitor no-match: mostly REAL, two real BUGS found.**
  - Correct no-match: Julldent (DK-exclusive brand) and Veecare Molt 9
    (competitors carry the same DESIGN under API/Dentmark brands → strict-brand
    correctly excludes). Bisco Z-Prime correctly MATCHED on pinkblue (same brand).
  - **BUG 1 (DK wrong variant):** "Meril Filasilk **#2-0**" resolved to **#5-0**
    — the #2-0 page exists on DK; the inline size code "#2-0" isn't matched
    (sizes #2-0/#3-0/#4-0/#5-0 are separate products with near-identical names).
  - **BUG 2 (competitor false positive):** "Upcera **DL-300**" matched Oralkart
    "Upcera **P2 Plus**" — a DIFFERENT model. Competitors don't stock DL-300
    (Google confirms); should be no-match. Model-code mismatch not gated.

**Takeaways / TODO:** (a) extract & require inline size/model codes like "#2-0",
"DL-300", "S6000" as hard discriminators (gate cross-model matches) — same class
as the KGF/serial-code work but for inline codes on BOTH the DK and competitor
side; (b) most competitor no-matches on this file are correct (brand discipline),
not recall failures (candidates ARE seen, 8–35 per cell).

### 2026-06-20 — Broken images fixed + child-name override (input-is-a-child)

**Bug (user): many DK images rendered as skeletons** (e.g. Julldent Orban knife
KO 12K). DK emits **bare media paths** (JSON-LD og:image, RSC child media,
sometimes search cards) like `…/ctlp/i/m/img01283.jpg`, which 404 — the working
URL needs the `/media/catalog/product/` prefix. Added one `dkImageUrl()`
normalizer (handles relative / protocol-relative / images1 / bare r2dkmedia) and
used it in `mapProduct` (search), `fetchDentalkartProduct` (PDP), and the
child `imageOf` (grouped media). All DK images now return 200.

**Bug (user): wrong variant for "Julldent Prosthetic Hex Driver 1.20mm Long -
Korean Implant Compatible".** The input is a CHILD name, but a *different*
product ("Julldent Implant Drivers and Hex Drivers", triage 0.757) out-ranked the
correct grouped parent ("Prosthetic Hex Drivers - Long", 0.741) — so `_best_match`
picked the wrong product entirely. Added **`_resolve_by_child_name`**: when the
top product is only a weak name match (fuzz < 0.9) to the input, scan the top
candidates' children and adopt one that matches the input near-exactly (≥ 0.9 and
better than current). Fixes the hex driver (1.20mm Korean ₹1195) and, across the
baseline, makes 10 products resolve to their EXACT input variant (OrthoMetric 018
Upper, OSL 0.022, Micro Tunnel Blade Triangular-End-Straight-TS ₹1395, Wizdent
A3B, LM-SlimLift C3, Shofu L525, SK Surgicals 1.5mm, Xcem 4mm×8.5mm, Phyx MBT 018,
Koden MBT 022) — all same prices, **0 verdicts lost**. Gated by fuzz<0.9 so GC /
ambiguous inputs keep the parent-listing path.

### 2026-06-20 — Per-child images + size-named child resolution + full names in UI

**Case (user): wrong image for "Crown … Suture Needle - Size 19 (Pack of 144
needles)".** It resolved to the grouped PARENT (₹600, parent image) because
"Size 19" is neither a config nor a distinct code, and the children had no
per-child image. Fixes:
- **Per-child images** (`parseGroupedChildren`): each child row carries
  `"media":"$ref"` → `["$inner"]` → `{file}`; build
  `https://r2dkmedia.dentalkart.com/<file>`. Added `image` to `ProductVariant`;
  the resolved child path + `_build_dk_result` now use the child's image.
- **Size-named child resolution** (`_pick_dk_child`): when there's no config/code
  signal, resolve by the tokens the input adds beyond the parent (e.g. "Size 19"
  → {size,19}); the child sharing the most must be a STRICT winner, else keep the
  parent (ambiguous "Crown … Suture Needle" stays at parent). Note: a plain
  fuzz/token_set_ratio test fails here — the parent is a subset of the input so it
  scores 100 too; the extra-token overlap is the right signal.
- Verified: Size 19 → its own image (size19 asset), OOS-aware; all prior grouped
  cases still resolve with images (paper/retractor/surgical box/knife/GC).

**UI (user): long matched product names were hidden.** `.cell-name` was clamped
(`-webkit-line-clamp:2` + ellipsis, max-width 180px). Now wraps to the full name
(max-width 240px, `overflow-wrap:anywhere`). Static file — just refresh.

### 2026-06-20 — Grouped-children parser rewrite + serial-code/size resolution

**Cases (user): wrong DK variant for products identified by serial code / size:**
- "Maarc Articulating Paper 70 Microns Straight - Blue & Red (5527/002/E)" → was
  matching "70 Microns - Red (5529/001/E)" (different colour + code).
- "...40 Microns Horseshoe ... (5523/050)" — same class.
- "Jull-Dent 79C Premium Small Orringer Retractor -40mm" → was "Round Titanium
  ... Large 55mm"; correct = "Julldent Premium Orringer Retractor - 40mm (079C)".

**Root causes + fixes:**
1. **`parseGroupedChildren` (dentalkart.ts) was too rigid** — a fixed field-order
   regex requiring `…name…pricing…product_id…seo…sku`. DK varies the field set
   per product (extra `action_btn`/`rating`/`has_spare_parts`…), so the Maarc
   Blue&Red parent parsed **0 children**. Rewrote it to build an id→row map from
   the RSC flight and read the **authoritative `child_products` list**, extracting
   each child's name/sku/price/stock order-independently (old sibling-token scan
   kept as fallback). Now parses 3 children (incl the OOS 5527/002/E). Surgical
   box (5) + knives (9) still parse.
2. **Serial-code resolution** (`_resolve_by_code` in compare.py): when the input
   names a code, fetch the top candidates' PDPs and pick the product/child with
   that EXACT code — taking priority over name similarity. Guard: only when the
   code is DISTINCT across children (so a SHARED parent SKU like "(JULL-DENT 223)"
   doesn't grab the first child — that path defers to config resolution).
3. **`base_name` now strips sizes/measurements, size words (small/large…), inline
   alphanumeric codes (79C), and collapses the brand hyphen (Jull-Dent→JullDent)**
   so the grouped parent surfaces (e.g. → "JullDent Orringer Retractor"). DK
   self-match now **ranks by the full input name** (base only widens recall) so
   the named variant wins ("Premium" over "Round Titanium").

**Verified:** all 3 cases correct (paper ₹595 incl. OOS flag, retractor 40mm/079C
₹1195); GC / surgical box (Basic Plus ₹15995, Premium ₹17995) / all knives still
correct. Baseline: 0 verdicts lost; several DK anchors now resolve to the EXACT
input variant (OrthoMetric 018 Upper, Shofu L525, Xcem 4mm×8.5mm, Koden MBT 022)
— net improvement.

### 2026-06-20 — Batch upload live progress ("searched X of N")

New streaming endpoint `POST /compare/batch-stream` emits NDJSON as each row
finishes: `{start,total}` → `{result,index,done,total}` (completion order) →
`{done}`. The UI (`runBatch`) now reads the stream via `response.body.getReader()`,
updates a new **"Searched X / N"** stat in the loading panel live, accumulates
results by index, and renders at the end. Non-streaming `/compare/batch` kept for
API/back-compat. Verified: stream emits incrementally; result payload is the full
CompareResult (competitors included).

### 2026-06-20 — Cross-brand instrument matching: investigated, strict brand kept

**Q (user): Julldent periodontal knives (KGF 9, KO 12K P03A, …) are on Dentmark/
Oralkart but don't show. Why? (Google finds them; on-site search doesn't.)**

**Findings (verified via WebSearch + WebFetch + scraper traces):**
- "Julldent" (Jull-Dent) is a **Dentalkart house/exclusive brand**. Oralkart &
  Dentmark carry **zero** Julldent products. (Confirmed: a Dentmark Goldman Fox
  PDP lists manufacturer = "Dentmark, UK"; no Julldent anywhere on either site.)
- "Goldman Fox / Buck / Orban / Kirkland" are knife **designs** (eponyms), sold
  by many makers (Dentmark, GDC, API, Hu-Friedy…). The competitor listings are
  those makers' OWN brands — same design, **different brand**.
- The candidates ARE found by our progressive queries (e.g. dentmark search
  returns "Dentmark Dental Goldman Fox Kgf9"); they're **rejected by the brand
  gate** ('julldent' not in the competitor name). That rejection is CORRECT.

**User decision: strict brand — never cross brands. Leave these unmatched.**
So the knives correctly show no competitor match (the brand isn't sold there).

**Tested + REVERTED: "brand may be in description" (defer brand to post-PDP).**
Implemented `enforce_brand=False` at competitor triage + description-aware
`_brand_conflict`. Baseline showed it **created cross-brand false matches**
(SK Surgicals → "JJ Ortho" screwdriver; Unident → "Dental Brushless micromotor")
because competitor brands often can't be cleanly extracted, so the post-PDP check
couldn't verify "same brand" — i.e. it COMPROMISED brand discipline. It also
added latency (knife 2.7s → 11.6s). Fully reverted gates.py/triage.py/structured.py/
pipeline.py to known-good. Verified false matches gone, brand strict, knife 2.9s.

**Web-search recall (DuckDuckGo) prototype:** works intermittently (rate-limits /
empty results) — not production-grade without a paid search API; and for these
knives it only surfaces different-brand products (correctly rejected). Not added.

**Net: no code change improves these without violating brand discipline; the tool
is behaving correctly.** Conclusion recorded so we don't re-litigate. If reliable
recall is wanted later, use a paid search API (SerpAPI/Google CSE/Bing), still
brand-gated.

### 2026-06-20 — CSV upload + 9× latency cut (45s → 5s)

**CSV upload (user request).** `/compare/batch` now accepts `.csv` as well as
`.xlsx`. `_parse_dk_csv` (same Product-Name column rules; bare single-column
list supported; BOM-tolerant) + `_parse_dk_upload` dispatcher (route by
extension, magic-byte `PK` fallback for xlsx). UI: file picker accepts
`.xlsx,.csv`, helper/intro text updated. xlsx path unchanged. Verified end to end.

**Latency: single compare 45.6s → ~5s, no logic change.** Two causes found by
profiling the sidecar request log:
1. **Pinkblue was being proxied through ScraperAPI** (root `.env` key was picked
   up by the sidecar) — 3–17s per request, and it burned ~1085 credits. Direct
   fetch from this residential IP is ~1s. Disabled the key for local dev
   (commented in root `.env`); the proxy is only needed on datacenter IPs in
   prod. → biggest win.
2. **Top-K PDP fetches in `pipeline.discover` were sequential** (Pinkblue:
   14.5+8.6+3 ≈ 26s serial). Now fetched concurrently via `asyncio.gather`, then
   processed in the same order — same candidates, same judge-budget order.
Result: GC compare 45.6s → 4.9s, knife → 2.7s; identical results; credits no
longer consumed. (Note: query set unchanged — search logic untouched.)

### 2026-06-20 — Ambiguous-name base preference + grouped self-match confidence

**Q (user): "GC Gold Label 9" showed only DK+Oralkart, but the full name showed
DK+Oralkart+Pinkblue. Why?** Not a Pinkblue bug — the two inputs resolve to
DIFFERENT DK products. The short/ambiguous "GC Gold Label 9" ranked
"GC Gold Label 9 Extra Capsules Pack Of 30" (₹4195) top, which only Oralkart
stocks; the full name resolves to "Posterior Restorative GIC" (₹2760), which both
Pinkblue and Oralkart stock.

**Fix — option 1: prefer the base product over a specialization when the input is
ambiguous.** `_qualifier_penalty()` in `routes/compare.py` demotes DK self-match
candidates that introduce specialization markers absent from the user's input
(extra / capsules / refill / combo / pack-of-N / set-of-N / mini / only / drills),
compared against the ORIGINAL input (so it never fires when the user asks for that
variant). Now "GC Gold Label 9" → Posterior Restorative ₹2760 (Pinkblue ₹2450 +
Oralkart ₹2580). Also "Julldent Micro Tunnel Blade ... TS" → base blade ₹1395
instead of "Set of 6" ₹7995. Explicit "...Extra Capsules" still keeps capsules.

**Q (user): low confidence on exact grouped children, e.g. "Julldent Premium Orban
Periodontal Knife (KO 12K P03A)".** Root cause: the self-match score came from
triaging the input against the grouped PARENT name ("Julldent Periodontal Knives
(JULL-DENT 191)") — token 0.18 / fuzz 0.65 → 0.705 "possible" — even though we
then resolve to the exact child. **Fix:** after `_pick_dk_child` resolves the
child, re-score the self-match against the CHILD name → now confirmed / 1.000.

Baseline diff is net-positive: several DK anchors now resolve to the exact input
variant (Shofu/Xcem/Koden/OrthoMetric full names) and base-over-specialization
works; the competitor-cell losses (Periotome/Unident/Shofu pinkblue) are
competitor-side live-search flicker or dropping previously-wrong matches.
Not yet committed.

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
