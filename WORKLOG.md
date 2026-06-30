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
  don't false-match. Same-code children disambiguate by name fuzz (041D forceps).
- **Micron thickness**: 40µ ≠ 70µ ≠ 100µ articulating paper (hard discriminator).
- **Instrument type**: forceps ≠ drills ≠ needle/holder/knife/chisel (two-sided).
- **Distinctive-token gate**: same-brand items sharing only brand + a generic
  noun (tray/paper/kit…) are different products (Tray Adeziv ≠ Eazy Tray).
- **Brand aliases**: tiny explicit same-manufacturer map (Avue = Dental Avenue).
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

### 2026-06-30 (pm-2) — Visual result cards, brand-gate fix, pinkblue self-heal, prod live

Built on the morning's work; all pushed (`8c00cec` → `94d9f01`) and **deployed +
verified on prod** (209.38.120.154:8000).

**UI — bulk / scheduled-run made attractive (Option B cards):**
- Bulk + scheduled-run results now render as **per-product cards** (one shared
  `compareCardsHTML`), so each product shows only ITS competitors — no sparse shared
  columns when Google returns different merchants per product. Each card:
  position-colored left accent stripe (green cheapest / amber mid / red priciest),
  big DK price, **plain-language verdict banner** (🏆/💰/⚠ "cheapest is <x> at ₹… —
  ₹… below DK"), KPI tiles (lowest / # cheaper / max saving / headroom), color chips
  with a ⭐ on the cheapest undercutter, expand → full competitor cells (✓keep/✗hide)
  + pricing-position table. Metrics come from ONE `productMetrics()` helper — the
  source the upcoming MCP insight charts will read.
- Removed the confusing low─DK─high price bar (replaced by the verdict banner).
- **Scheduled Runs list** redesigned from a flat table to **run cards** (status
  accent, Google chip, SKU progress bar, accuracy pill, hover-tint actions).
- Removed the per-product **price-history** table from the card expand (not relevant
  now; `/runs/history` endpoint left intact for later).

**Matching — brand is mandatory (name → description → else wrong):**
- Fixed a false positive: DK "**Julldent** Anterior **Gracey Curette**" matched
  amazon "Gracey Curette #1/2 Rigid" because the "competitor leads with the
  product-line word" rule fired on "**gracey**" — but gracey is a generic curette
  TYPE, not a brand. Added `_GENERIC_TYPES` (curette/scaler/forceps/probe/… +
  eponyms gracey/langer/mccall/columbia…) and excluded them from that rule.
  Distinctive coined lines (Ketac, Fuji) still pass. Verified via gate_check (6
  cases) + live (amazon now rejected). Regression rows added.

**Robustness — pinkblue self-heals on prod:**
- `smartFetch` now AUTO-falls back to ScraperAPI when a direct fetch to a
  datacenter-blocked host (pinkblue) is blocked/errors. Prod reads pinkblue with NO
  flag; local stays direct (no credits). `PROXY_PINKBLUE` is now just a speed
  optimization. Verified: prod pinkblue ₹2559 (was "couldn't verify").

**Prod parity — CONFIRMED LIVE:** reviewer set the droplet `.env`
(`DATABASE_URL`→Neon, new `SERPAPI_KEY`). Verified on prod: `/runs`=32 & `/reviews`
=71 (shared Neon), new SerpAPI key in use (0→usage), full GC compare matches local
exactly (buzzdent ₹2858, medidentalpro ₹2812, jaypee ₹2695, pinkblue ₹2559), UDS-E
≠ P rejected on amazon. New UI live on prod (pcard-verdict / run-card present).

How **✗hide** works (for reference): writes `confirmed_matches` label=no_match
(shared DB) → next compare short-circuits that competitor to "Confirmed: not sold
here" (no scrape). NOTE/OPEN: no one-click UN-hide in the UI yet (hidden cells lose
the ✓/✗ buttons) — add an un-hide affordance if wanted.

NEXT: MCP-driven insight visualizations (reuse `productMetrics`); optional un-hide
button; semantic-synonym LLM judge (needs Anthropic key); reviewer runs regression.

### 2026-06-30 — Multi-platform extraction, shared Neon DB, prod parity

Driven by real GC Gold Label 9 / Woodpecker UDS-E misses. Two themes: **(1) read
every storefront platform, not just WooCommerce; (2) make prod behave exactly like
local** (shared DB + env parity). All pushed (`658be27` → `0e51a82`).

**Extraction — don't be limited to known platforms:**
- **Images** (`lib/pdp.ts`): schema.org `image` can be an `ImageObject`/array of them;
  `String()` produced `"[object Object]"` → broken thumbnail. `jsonLdImageUrl()`
  unwraps `url`/`contentUrl`/`@id`. Fixed dentosky, onlinedental, medidentalpro.
- **Sub-variants per platform** (`lib/scrapers/generic.ts`): a page's single JSON-LD
  price is the default/cheapest, so we showed the wrong pack. Added **Shopify**
  (`/products/<handle>.json` — buzzdent ₹1424 Mini → ₹2858 (Extra) Big Pack) and
  **Magento** (`jsonConfig` — medidentalpro ₹1379 → ₹2812 Big Pack (Extra)) on top
  of the existing WooCommerce path.
- **Amazon** (`parseAmazon`): ships NO JSON-LD/OG (not anti-bot — a residential IP
  gets 200; it was a parsing gap). Dedicated DOM parser: `#productTitle`, the deal
  `.a-offscreen` in `#corePrice_feature_div`, `#landingImage`, feature-bullets,
  INR/foreign guard; direct → ScraperAPI (droplet IP captchas). Verified on 4 ASINs.
- **JSON-LD ProductGroup** (`findProductNode`): jaypeedent wraps priced `Product`
  nodes in a `ProductGroup.hasVariant[]` → recurse in (was "couldn't verify" →
  ₹2695).
- **Platform-NEUTRAL fallback** (`parseSchemaVariants`): after the 3 platform
  parsers, fall back to schema.org `hasVariant` so a store we have NO parser for
  still resolves variants (verified: dentalprod). Shopify `.json` probe now fires on
  any `/products/<handle>` URL (custom-domain Shopify). Base extraction
  (name/price/image/desc) was already platform-agnostic via JSON-LD/OG/microdata
  (verified: shristigroup custom cart). Structure-less SPA / parked pages correctly
  stay "couldn't verify" (no wrong price).
- All catalogued in `docs/MATCHING_EDGE_CASES.md` (§F 19a/22a, §G 24/25a/25p, §I-amazon).

**Shared database — local & prod now ONE store (Neon Postgres):**
- The run-store (runs, run_items, reviews, watchlist, **confirmed_matches** learning
  loop) was per-machine SQLite → local & prod diverged. Ported `run_store.py` to the
  shared Neon DB: **identical public API** (all 31 call sites unchanged), per-call
  psycopg conn against Neon's `-pooler` endpoint (works same on macOS + Linux;
  psycopg_pool's worker threads hit a macOS DNS bug, so avoided), self-created schema
  (CREATE IF NOT EXISTS), `list_runs` N+1 batched.
- Migrated all data into Neon: **32 runs, 857 items, 71 reviews, 14 confirmed, 5
  watchlist** (ids + sequences preserved; write/read/delete verified). Neon conn
  string validated; alembic `upgrade head` ran clean (pgvector enabled).

**Prod parity (`scripts/deploy.sh`, sidecar, `.env.example`):**
- Sidecar `.env` now resolved relative to the file (repo root), NOT cwd — a different
  systemd WorkingDirectory would silently drop `SCRAPER_API_KEY`/`PROXY_PINKBLUE`
  and break every datacenter-IP-blocked site on prod only.
- `deploy.sh` now health-gates the **sidecar (3100)** too, not just the API — a dead
  sidecar no longer "passes" while all extraction is broken.
- New `api/.env.example` + rewritten root `.env.example` document every required var.
- New SerpAPI key (Free, 250/mo) in local `api/.env`.

**Prod tests (droplet):** core + Google paths work; today's fixes are LIVE
(jaypeedent ₹2695, buzzdent ₹2800, medidentalpro ₹2624, UDS-E≠P rejected on amazon).

NEXT — droplet `.env` still to set by reviewer (then `bash scripts/deploy.sh`):
1. `api/.env`: `DATABASE_URL` → the Neon URL (prod `/runs`=0 ⇒ not on shared DB yet);
   `SERPAPI_KEY` → new key `26731cc5…` (new key shows 0/250 used ⇒ prod on old key).
2. root `.env`: `PROXY_PINKBLUE=1` (pinkblue "couldn't verify" on prod without it).
3. Then re-run prod tests to confirm 14 confirmed matches + new key in use.
Open: semantic synonyms (LLM judge, needs Anthropic key); reviewer runs regression.

### 2026-06-29 (pm-6) — Read more pages + brand-by-description + Extra-flag + spec-from-description

Long debugging session on real misses (Ketac Molar, GC Gold Label 9, sterilization
reels). Theme: **the right info exists on competitor pages, just in different
places** — title, description, variant list, spec table. Shipped (all pushed):

- **Pack/dimension sanity**: "8 Tips Free" ≠ pack-of-8 (freebie strip); "150 MM x
  200 M" ≠ pack-of-200 (the `x`-pack pattern now excludes m/mtr/meter — a bare
  "200" was tanking the per-unit price and false-rejecting reels). Recovered
  PinkBlue + Ayushi reels.
- **JSON-LD extraction**: salvage now also strips raw control chars (dentganga);
  accept full-URL `@type` ("http://schema.org/Product", hospitalstore); OG /
  microdata / `<h1>` fallbacks.
- **Brand when the maker is DROPPED** (`gates._brand_match`): accept when the
  competitor leads with DK's product-LINE word ("Ketac Molar" for "3M ESPE Ketac
  Molar") OR the brand is in the DESCRIPTION intro ("…by 3M ESPE"). Guarded vs
  "compatible with X" and genuinely different brands. Recovered dentalbucket.
- **"Extra"/formulation difference FLAGS, doesn't HIDE** (`structured.py`): a
  competitor selling the same item as "HS / High Strength" (= GC's Extra line) or
  omitting "Extra" is now BORDERLINE+⚠, not rejected. Recovered 4 GC Gold Label 9
  sellers.
- **Spec parsed from name + DESCRIPTION** (`generic.ts`): powder g / liquid ml /
  pack now read from the body's first 300 chars too, so terse titles ("GC 9 big")
  pick up specs in the description.
- **Currency/foreign filter** (non-INR dropped); **WooCommerce variation
  extraction** (Ayushi reel sizes); **single-letter (UDS E≠P) + short alnum (V2)
  model codes**; **tooth-position** contrast; **near-name not hard-rejected on a
  price gap**.
- **ScraperAPI key refreshed** to a fresh 5,000-credit key (root .env, gitignored).
- **`docs/MATCHING_EDGE_CASES.md`** created — living catalog of all ~30 cases.

State established (the matching pipeline now): brand by name → product-line →
description; then numerical/spec matching across name + description vs DK; price
only shown when page-verified; borderline → ⚠ flag; ✓keep/✗hide learns.

NEXT (tomorrow):
1. **Extraction gaps** (per-platform, addable): Magento configurable variants
   (`jsonConfig`) so medidentalpro picks "Big Pack (Extra)"; HTML spec-table parse
   for jaypee (price/specs in a table, not JSON-LD); amazon is anti-bot (hard).
2. **Semantic synonyms** ("Gold Label 9 = Fuji 9 = Type 9", "ART ≠ plain") — rules
   can only ⚠-flag; the **LLM judge/extractor** (already wired, `llm_judge.py`) is
   the general fix — needs an Anthropic API key (Max plan ≠ API).
3. **Production parity**: droplet `.env` needs `SERPAPI_KEY`, `SERP_ENABLED=1`,
   `SCRAPER_API_KEY=49b12c27…` (new key), and **`PROXY_PINKBLUE=1`** (datacenter IP
   blocks pinkblue) — then verify on http://209.38.120.154:8000.
4. **Brand-drift on brand-LESS queries** (DK→Waldent vs market→Oro) — decide the DK
   anchor behaviour.
5. Reviewer to run the regression suite. **Credits:** SerpAPI ~53/250 left;
   ScraperAPI 5,000 (fresh).

### 2026-06-29 (pm-5) — Sub-variant on variable products + size tokenization + UI declutter

Triggered by Ayushi Density sterilization reel (wrong size price) + UI feedback.

- **Sub-variant for WooCommerce variable products** (`lib/scrapers/generic.ts`): the
  generic reader now extracts `data-product_variations` (size + price for every
  variation) into `variants[]`, so the existing Python `select_variant` can pick
  the one matching the DK size. Previously only the DEDICATED scrapers populated
  variants, so generic merchants returned the base/default price. Ayushi now
  resolves all 6 reel sizes correctly (150MM → ₹1,770, etc.).
- **Size tokenization** (`tokens.py`): JOIN a number + unit into one token
  ("150 MM" → "150mm") so the spaced and glued forms match (DK "150MM" ≡ variant
  "150 MM x 200 M") AND the unit stays bound — "200mm" (width) ≠ "200m" (length),
  which was making a bare "200" match the wrong reel. Model codes (EXS6, V2) stay
  whole (letter-first). Fixed all 6 sizes incl. the 200MM/200M collision.
- **UI declutter** (`index.html`): removed the dead 👍👎⭐ feedback row (Postgres,
  duplicated ✓keep/✗hide); long names clamped to 2 lines (full on hover); price +
  Δ on one line, verdict · score · view on one line; tighter row padding. The
  Google toggle's help text moved to a CSS hover tooltip (the native title was
  unreliable). Competitor COLUMNS now order matched-first (most matches first),
  then unmatched/other — stable within each group.

Reviewer to run the regression suite.

### 2026-06-29 (pm-4) — Freebie-pack false-reject + JSON-LD @type-URL + short model codes + near-name softening

Triggered by "Woodpecker UDS E LED (8 Tips Free)": Dental Bucket / IDS Denmed
wrongly "different", Hospital Store "couldn't verify", UDS-E≠DTE-V2 only borderline.

- **Freebie miscounted as pack** (`lib/pack-detector.ts`): "8 Tips **Free**" was read
  as pack=8 → DK's per-unit price looked 8x off → false reject of the SAME scaler.
  Now strips freebie phrases ("N <unit> free", "free N <unit>", "N free <unit>")
  before counting. Real packs still count ("Pack of 100"→100). Fixes Dental Bucket
  + IDS Denmed (→ ₹15,500, match).
- **JSON-LD @type full-URL form** (`lib/pdp.ts`): `findProductNode` only matched
  `"Product"`, silently skipping `"http(s)://schema.org/Product"` /
  `"IndividualProduct"`. A whole class of sites (e.g. hospitalstore.com) was
  unreadable. Now matched → Hospital Store reads ₹18,900 (via ScraperAPI proxy
  that bypasses its 403 + this parse fix).
- **Short alphanumeric model codes** (`attributes.py`): "V2" (1 letter+1 digit) fell
  through `_MODEL_RE` (needs ≥2 digits) and `_SKU_RE` (needs ≥3 letters). Added
  `_ALNUM_CODE_RE` (letter-led `[a-z]{1,2}\d{1,2}` so units "5g"/"10ml" never match).
  Now "Woodpecker UDS-E LED" ≠ "Woodpecker DTE **V2**" (REJECTED, was borderline);
  bonus: shade A2≠A3.
- **Near-identical name no longer hard-rejected on price gap** (`structured.py`): the
  extreme-unit-price reject now spares a near-exact name match (fuzz≥confirm or
  token≥0.60) — that gap is a pack/form/bundle difference, not a different product;
  it's shown + ⚠-flagged instead. Weak-name lookalikes still rejected.

Net for the Woodpecker product: verified prices went 2 → 6 (Dental Bucket, IDS,
Hospital Store, PinkBlue added). Known limit: pure-SPA pages with NO structured
price (dentalstores.in — price is plain-text MRP) stay "couldn't verify" (no wrong
price); reading those needs the AI extractor (parked — no Anthropic key). Reviewer
to run the regression suite.

### 2026-06-29 (pm-3) — Single-letter model discrimination (UDS E≠P) + ScraperAPI decoupled

Triggered by "Woodpecker UDS E LED" matching UDS-P listings.

- **Single-letter model designator gate** (`attributes.py`): a standalone UPPERCASE
  letter ("UDS **E**" vs "UDS **P**", "Type A" vs "Type B", "D Speed" vs "E Speed")
  is now captured as a model code (`ml_e`, `ml_p`), so the existing model-code gate
  rejects differing letters while tolerating one side omitting it. Articles A/I and
  dimension X excluded. Verified: UDS-P → REJECTED, all UDS-E variants MATCH; no
  regression on J-Morita (initial), Kids-e-Crown, S/M sizes.
- **ScraperAPI decoupled from pinkblue** (`lib/http.ts`): routing pinkblue through
  the proxy is now gated on a separate `PROXY_PINKBLUE` flag (set only in prod).
  So `SCRAPER_API_KEY` can be enabled in DEV for the generic fallback WITHOUT
  breaking pinkblue (which works direct locally). Key enabled in local .env
  (gitignored); `PROXY_PINKBLUE` left unset locally.
- **Generic fallback stays cheap** (`generic.ts`): direct fetch → ScraperAPI PROXY
  (no JS render). Deliberately NOT escalating SPA pages to a rendered fetch —
  render costs ~25 credits and those pages usually ship no structured price, so it
  would drain credits for nothing.
- **No body-text price scraping** (`pdp.ts`): removed — on real pages it grabbed
  the struck-through MRP or concatenated digits (dentalstores.in → "2500024"). A
  page with no STRUCTURED price now stays UNVERIFIED (correct: a wrong price is
  worse than none). Kept harmless name fallbacks (<title>, <h1>). Reading such
  JS-app pages correctly needs an AI extractor — parked (no Anthropic API key).

DECISION: AI extractor + LLM judge (the self-adapting path that would end the
per-site patch cycle) is PARKED — needs an Anthropic API key (the Max plan does
NOT cover programmatic API use). Staying with rules + ScraperAPI proxy + the ⚠
review flag + ✓ keep / ✗ hide learning. Reviewer to run the regression suite.

### 2026-06-29 (pm-2) — Verify unreachable PDPs + foreign-site filter + per-cell action + Google-order columns

Follow-ups driven by real cases (thedentistshop "couldn't verify", IPG Dental foreign listing).

- **Verify previously-unreachable competitor PDPs (general, any merchant):**
  - `lib/pdp.ts` `parsePdpHtml` now SALVAGES malformed JSON-LD (strips JS `//` and
    `/* */` comments + trailing commas) — recovers thedentistshop (its Product
    block had a `//` comment → ₹5100 now reads, FREE, no API). Added price
    fallbacks: `og:price:amount`, `itemprop=price` (content/text).
  - `lib/scrapers/generic.ts`: ScraperAPI FALLBACK — if the direct fetch yields no
    usable product (blocked datacenter IP / needs JS), retry via ScraperAPI
    (`scraperApiUrl` in `lib/http.ts`). Fires only on hard cases to save credits.
  - ScraperAPI key (root .env) refreshed to the working one (308 credits left,
    resets 2026-07-17) but KEPT COMMENTED in dev — enabling it routes pinkblue via
    ScraperAPI which fails locally + burns credits; it's a PROD/datacenter thing.
    The salvage fix means thedentistshop works locally without it.
- **Foreign-site filter:** `pdp.ts` captures `priceCurrency`; when absent it infers
  from page symbols (₹/Rs/INR → INR keep; €/£/EUR/GBP/AED/CAD… → foreign). `generic
  .ts` drops any non-INR page. Kills IPG Dental's EUR "Localizador Root ZX Mini"
  (it declared no currency, html lang faked en-US, but the page is € — now dropped,
  no bogus ₹860). All competitors are Indian (gl=in), so this is safe.
- **Per-cell action → learned memory:** every competitor cell now has **✓ keep**
  (remember this link) and **✗ hide** (store a no-match → competitor stops showing
  for this product name). New endpoint `POST /reviews/cell`; UI `confirmCell`.
  Verified: ✗ hide on a wrong listing → next run shows "Confirmed: not sold here".
- **Competitor columns now in GOOGLE'S top-10 order** (`competitorColumns`): union
  across rows by first-appearance = Google Shopping rank (single search = exact
  Google order), instead of forcing the baseline 3 first.

Reviewer to run the regression suite. (.env is gitignored — ScraperAPI key not committed.)

### 2026-06-29 (pm) — Learning loop: confirmed-match memory + auto-flag review

Built the #4+#5 plan the product owner asked for: flag what needs a human look,
remember every human confirmation, and reuse it next time.

- **Confirmed-match store** (`run_store`): new SQLite `confirmed_matches` table keyed
  by NORMALIZED dk name → per-competitor {label correct|no_match, matched_url,
  matched_name}. `upsert_confirmed` / `get_confirmed` / `clear_confirmed` /
  `confirmed_count`. We store the LINK, not the price.
- **Write path** (`routes/reviews`): a ✓-correct row now stores each page-verified
  shown match (url + price, no reject note) as a confirmed link — "learned: N" in
  the response. Verified end-to-end via POST /reviews (the "Different product" cell
  was correctly NOT stored). The PG-backed /feedback + /golden paths were left
  alone (Postgres isn't running here).
- **Read path** (`routes/serp`): `serp_compare` consults the confirmed memory FIRST
  — `_confirmed_match` re-scrapes the stored URL for a FRESH price and returns a
  "Confirmed by you ✓" match; a dead/changed link (fetch fails or now structured-
  rejects) falls back to live discovery. `no_match` confirmations short-circuit to
  "Confirmed: not sold here".
- **Auto-flag** (`_flag_review` + `CompetitorMatch.needs_review/review_flags`): a
  shown match is flagged for review on real signals only — verdict=possible,
  spec=different-size, cosine<0.6, or price ≥2x off DK. Tuned to NOT flag clean
  exact matches (initial version wrongly flagged same-tier/unknown). UI shows a
  "⚠ review" badge per cell + a "⚠ N need review" pill in the results summary;
  "Confirmed by you ✓" cells render green and are never flagged.

Net: accuracy compounds (confirmed links stick, mistakes get re-checked) and the
reviewer's attention is steered to the few flagged cells instead of every row.
Reviewer to run the regression suite.

### 2026-06-29 — Compatibility-part guard (fusion-robust) + bulk Google top-10 UX

Continuing the pending list from 06-26.

- **Compatibility-part fix** (`gates._brand_compat_only`): a third-party part like
  "Dental Apex Locator Main Cable **For** E2ZZ, J-Morita" was matching the genuine
  "J Morita … Accessories". The fitment guard already existed but `normalize_for_match`
  fuses "J-Morita" → "JMorita", so it looked for the word "morita" and missed it.
  `_locate()` now also matches a brand fused onto a 1–2 letter INITIAL (jmorita ⊇
  morita), so the guard fires. Verified: cable→REJECT; real "Morita ZX … Accessories"
  →PASS; brand-at-start "… Cable for Root ZX"→PASS; "GC Fuji by GC"→PASS. Live: the
  Dentmark cable now drops; only genuine accessories (PinkBlue ₹4767, Oralkart ₹4800)
  show prices for the J-Morita test.
- **Bulk Google top-10 (UX only — backend already worked):** `/compare/batch-stream
  ?serp=1` already routes each product through `serp_compare` (now top-10), capped at
  `_SERP_BATCH_CAP=15`, and `runBatch` already honours the Google toggle. Fixed the
  stale UI: the toggle no longer claims "batch always uses standard search" (it
  applies to both), and the confirm dialog now states the real cost (~8 searches/
  product, up to ~120 for the 15-cap) instead of the old ~2/~30.

Still pending: reviewer regression run; automated accuracy harness for volume;
confirmed-link cache + cross-competitor variant alignment. (Dropped: the
product_results.stores seller-list gate — superseded by the top-10 work.)

### 2026-06-26 — Top-10 Google competitors + Shopping-gate fail-open + brand/variant precision

Two themes: a big new feature (dynamic top-N competitors on the Google path) and a
batch of brand/variant precision fixes triggered by real examples. Reviewer to run
the regression suite.

**Google Shopping gate — fail OPEN (bug fix).** `serp_shopping_sources` returned an
empty set on ANY failure (quota/429/error/empty payload); the route read that as
"every competitor absent" and stamped whole runs "Not on Google Shopping". It now
returns three-state `set | None` — None = lookup failed → the route FAILS OPEN
(matches normally) instead of hiding everyone. Root cause of the "everything says
Not on Google Shopping" reports was this + a uvicorn started without `--reload`
(stale code); restarted with reload.

**Top-10 competitors (new feature, Google path only).** Beyond the fixed 4, surface
the top merchants Google Shopping lists, each verified through the SAME matcher.
- `serp.serp_top_competitors(name, limit=10)`: 1 shopping search (ranked merchant
  list) + 1 organic search (free PDP urls for ANY domain) + 1 immersive search per
  NEW merchant lacking a free url (to get its direct PDP). The 3 baseline
  competitors are always shown. Cost ≈ 8 searches/product for a fresh item ("verify
  all, no cap" per product owner).
- `lib/scrapers/generic.ts` + sidecar `productFetchers.generic` (and unknown
  `?scraper=` falls back to it): a generic PDP reader (JSON-LD/OG via `parsePdpHtml`)
  for merchants with no dedicated scraper. Same pack/unit normalization.
- `routes/serp._match_competitor` unified for known + new merchants: known get
  own-site search + dedicated fetch; new get organic/immersive urls + generic fetch.
  Everything after the fetch (select_variant → structured_match → triage) is identical.
- UI (`index.html`): competitor COLUMNS are now built dynamically from the data
  (union across rows), horizontally scrollable, with min-widths so 10 columns don't
  squish. Standard `/compare` path still renders the fixed 4.

**Strict verification policy (precision).** A price is shown ONLY when we read the
seller's OWN page and it passed every check. Layered outcomes:
- read + all checks pass → verified price.
- read + matcher rejects → "Different product on this site" (no price).
- page unreadable → "Listed on Google — couldn't verify page" (NO price — we never
  show a Google card price we couldn't verify on the page).
- card title clearly different / price 8×+ off DK → "Different product …" (no price).
Also added a price-band guard on loose ("possible") verified matches: an 8×+ gap
from DK = different variant (the ₹41k "Root ZX Mini" unit dropped for a ₹5k accessory).

**Brand & variant precision (broad, principled).**
- `attributes.extract_brand`: (a) a lone single-LETTER first token is a brand
  INITIAL, optional — "J Morita" → brand "morita" (so a competitor's "Morita ZX…"
  matches), guarded so size/grade letters ("S Cartridge", "D Speed") keep the
  letter; (b) a coined MULTI-hyphen first token is kept whole — "Kids-e-Crown" →
  "kidsecrown", not the fragment "kids" (single-hyphen "LM-SlimLift"→"lm" unchanged).
- `gates._BRAND_ALIASES`: manufacturer⇄line knowledge — `kidsecrown ⇄ shinhung`
  (Kids-e-Crown is Shinhung's line) so a "Shinhung …" listing still matches.
- `structured._brand_conflict`: now alias-aware (consistent with the gate) so the
  deep check doesn't reject a same-brand product the gate accepted.
- `gates._CONTRAST_GROUPS`: added TOOTH POSITION (central/lateral/canine/premolar/
  molar). Fixes Kids-e-Crown "Canine" wrongly matching Medidentalpro's "Central".

`api/pyproject.toml` got a `[tool.vercel] entrypoint` line while exploring a Vercel
deploy — note: this app is NOT serverless-deployable (persistent :3100 sidecar +
SQLite + torch); the right host is a container with a disk. Entry left in harmlessly.

### 2026-06-25 (pm) — Run #38 accuracy fixes + Google Shopping gate

Worked the 17 issues logged in run #38's accuracy review. Broad fixes (each as its
own commit; reviewer to run the regression suite):
- **Serial/number precision** (`gates.py` `_number_conflict` + `_NUM_SIG_RE`): a
  general numeric/serial signature — any differing number/serial/decimal/fraction
  splits two near-identical names, kept WHOLE with its suffix (1.099-1 ≠ 1.099-2,
  1.861 ≠ 1.862). Replaces per-format regexes; also added `_DECIMAL_SIZE_RE`
  (articulator 3.5 ≠ 4.5).
- **Multi-word-brand distinctive gate**: strip the shared leading brand prefix
  (≤2 words) so "Tor Vm"'s tail "vm" stops masking as a shared distinctive token
  (Tor Vm Proxicut ≠ Polishing Discs).
- **Generic noun = actual product identity when it DIFFERS**: only drop a generic
  noun (pouch/box/kit) when BOTH names share it; keep it when they differ (Reel ≠
  Pouch). Plus stem plurals (reels == reel).
- **All-generic candidate rejected**: a candidate sharing NONE of the input's
  distinctive words is different even if its own name is all brand+generic+
  stopword ("Oro Dental Kit" ≠ "Oro Sterilization Reel").
- **Single-letter model prefix kept whole** (`normalize.py`): "i-Scan" → "iScan"
  so the model identity doesn't dissolve into a shared "scan" (i-Scan ≠ Free
  Scan; oralkart now matches the real i-Scan). Ora-Craft / 2-0 / TR-13 untouched.
- **Google Shopping gate** (`serp.py` `serp_shopping_sources` + `routes/serp.py`):
  management decision — only show a competitor if it's listed on Google Shopping.
  Competitors absent show "Not on Google Shopping" (CompetitorMatch.note, rendered
  in cell + ⇄ compare view). Costs +1 SerpAPI search/product.
  KNOWN LIMITATION: the gate reads the single headline `source`; multi-seller
  cards (`multiple_sources=True`, "& more") hide other sellers, so it both
  under-counts and varies vs the live page. The FULL seller list is available via
  the immersive product API (`product_results.stores`) — needs wiring for accuracy.
- **Configurable Google re-run limit** (`routes/runs.py`): `?serp=1&limit=N`
  (0/≥count = all) so a Google re-run can cover more than 15 products; UI prompts
  for the count. Plus `SERP_SITE_FALLBACK` was briefly off then reverted on.
- SerpAPI key rotated again (fresh 250/month, api/.env).

NEXT: wire the accurate Google-Shopping seller-list gate (`product_results.stores`)
so the "Google-Shopping-only" rule counts ALL sellers, not just the headline.

### 2026-06-25 — Brand "compatible-with" guard, Google-rerun pairing, scheduler off

- **Brand compatibility-reference guard** (`gates.py`): a brand mentioned only as
  a fitment note — "Dental Apex Locator Cable **For E2ZZ, J-Morita**" — is a
  third-party part that FITS J-Morita, not a J-Morita product. `_brand_compat_only`
  rejects a brand that appears only after a compatibility marker (for/fits/
  compatible/suitable/replacement/spare) and not in the first ~3 words. Brands at
  the start, and "by/from" attributions (GC Fuji by GC), still pass. 37/37
  regression (added J-Morita + Dentsply-Protaper cases).
- **Google re-run pairing** (`routes/runs.py` + UI): a Google re-run now pins its
  comparison base to the nearest STANDARD ancestor (always Standard L vs Google R,
  never Google-vs-Google) and the UI auto-opens the ⇄ compare when it finishes —
  no hunting for the new run. Verified: Google-rerun of #29 → standard #28.
- **Automatic scheduler turned OFF** (`SCHEDULED_RUNS_ENABLED=0` in api/.env, local
  config only). Manual "Run now", Google re-runs, uploads, compare all unaffected.
- **SerpAPI key rotated** twice to fresh 250/month keys (api/.env, gitignored).
  Earlier keys hit quota / one was invalid (24-char, not a real key).
- **Jira draft** (story + 6 merged sub-tasks, plain-English) prepared for manual
  entry — covers the 2-day effort + the to-do (accuracy harness, confirmed-link
  cache, cross-competitor variant alignment). No Jira MCP connected, so not filed.

### 2026-06-24 — UI/UX batch + diff-aware contrast gate + scraper price fix

Grouping several same-day changes that landed as their own commits:
- **Diff-aware contrast gate** (`gates.py`): generalized intraoral/extraoral into a
  list of variant AXES (upper/lower, left/right, anterior/posterior, mesial/distal,
  buccal/lingual, straight/curved/angled, small/medium/large, fine/coarse,
  single/double, pediatric/adult, restorative/luting, …). Different values on the
  same axis ⇒ different product. Catches straight/curved (needle holder 073A/073B),
  left/right (Warwick James), etc.
- **pinkblue price** (`lib/scrapers/pinkblue.ts`): the rendered Magento price box
  (data-price-amount / data-final-price) now WINS over JSON-LD offer.price, which
  is a stale/aggregate value on grouped/special products (Ketac Molar ART Kit:
  JSON-LD 2680 vs real 1676). Sidecar restarted (tsx, no build).
- **Scheduled Runs UI**: pagination (10/page, Prev/Next), permanent delete
  (`DELETE /runs/{id}` + 🗑), live auto-poll after Run now, ↻ Refresh button, and
  view-persist via `#runs` hash so a reload doesn't drop to the homepage.
- **Bulk uploads persist as runs**; the compare-page accuracy review links to that
  run; **accuracy review added to the ⇄ compare view** (reviews the R run).
- **Render robustness**: a row missing `competitors` no longer aborts
  renderResults (which had hidden the table + the ✓/✗ + Submit review controls);
  table renders before insights; clearer "✓ correct / ✗ needs fix" labels.
- **⇄ compare view**: DK shown for both L/R, products in only one run listed
  separately (untested ≠ missed), full names (no truncation), product thumbnails
  (+ exact child image via select_variant).
- **Pricing insights**: each category tag is clickable → lists the exact products
  with DK + competitor links.
- **SerpAPI key** rotated to a fresh 250/month key (api/.env, gitignored).

### 2026-06-24 — Canonical-core search (the long-name reliability fix) + cement-function contrast

The "middle reliable solution" for huge names: the SerpAPI hybrid own-search was
querying competitors with the FULL verbose name ("3M ESPE Ketac Molar … (15g
Powder + 7.8mL Liquid + Mixing Pad + Scoop)") → 0 hits. Switched it to the
CANONICAL-CORE smart queries (brand+line+key tokens) and rank hits by name
similarity → 0→22 (pinkblue) / 19 (oralkart) results, the right products found.
(`routes/serp.py` `_match_competitor`.) Standard /compare already used smart
queries; this brings the Google path to parity.
This surfaced a precision bug: oralkart matched "Ketac CEM … Luting Cement" (a
luting cement) for a "Ketac Molar … Restorative" (filling) — caught with a new
contrast axis {restorative, luting}. Result: pinkblue Ketac Molar ART ₹1676 +
oralkart Ketac Molar GI Filling ₹1750, both correct. 35/35 regression.

### 2026-06-24 — Ortho-elastic discriminators: intraoral/extraoral + Oz force + fraction-inch

"Penta Ortho Intraoral Elastics 5/8 -3.5 Oz" matched "…Extraoral… 3/8-8 Oz" at
0.93 — three distinguishing features, NONE captured: gate passed, names ~94%
cosine. Added three independent discriminators (33/33 regression):
- `_opposite_category` gate: intraoral ≠ extraoral (XOR — a name mentioning both
  doesn't trip; non-letters stripped so intra-oral == intraoral).
- `_OZ_RE`: ounce force/volume "3.5 Oz"/"8 Oz" → model_code "<n>oz" (3.5oz ≠ 8oz).
- `_FRAC_RE`: fraction-inch size "5/8"/"3/8" (denom 2/4/8/16/32 only, so ratios
  "1:1", suture "/0", dates don't match) → model_code "<n>/<d>".
Each alone splits the pair; the two-sided model-code gate + category gate make it
robust. Safety: same-oz / same-fraction / 1:1-ratio still pass.

### 2026-06-24 — SerpAPI path is now hybrid (Google URLs + competitor own-search)

"Life Stericab UV Chamber": Google missed pinkblue entirely and returned only
oralkart's pricier "with Intensity Meter" upgrade (₹9325), not the ₹6500 base —
because Google's index of these niche competitor sites is incomplete. Fix:
`_match_competitor` now evaluates Google's discovered URLs PLUS the competitor's
OWN search results (merged, deduped, top 6) through the matcher and keeps the
best. Recovers the right base variant + pinkblue. Result: oralkart ₹6500 (base) +
pinkblue ₹8041 + DK ₹8200. The Google path is now ≥ standard (a superset), so the
⇄ compare view shows where Google's URLs add recall beyond own-search.

### 2026-06-24 — SerpAPI path: resolve DK via our own search, not Google

"Life Stericab Ultra Violet (UV) Chamber" came back with DK=NONE in the Google
path: Google's top dentalkart result was the WRONG product ("Life Steriware …
Storage Cabinet"), and that page even failed to fetch. But DK's own site search
nails it (₹8200). Fix: `serp_compare` now resolves the DK anchor with `_resolve_dk`
(+ `_dk_has_input_product` guard) exactly like /compare, and uses Google only for
the harder-to-search COMPETITORS. Best of both: reliable DK + Google competitor
recall. Life Stericab now: DK ₹8200 + oralkart 0.927 (pinkblue still a Google
index gap).

### 2026-06-24 — Bulk upload honours Google toggle + persists as a run; Google runs distinguishable

- **Bulk upload → Google**: the Quick-search Google toggle now also drives the
  batch upload. `POST /compare/batch-stream?serp=1` routes each product through
  `serp_compare`, capped to the first 15 (`_SERP_BATCH_CAP`) for quota; the UI
  confirms the cost first and badges the result "🔍 via Google (first 15)".
- **Uploads persist as runs**: every batch upload now creates a run
  (`trigger=upload` / `upload-google`), saving each item as it completes and
  finishing on stream end — so a main-page bulk test shows up in the Scheduled-
  Runs history (reviewable / re-runnable / comparable) like any other run.
- **Google runs distinguishable**: runs list shows a blue "🔍 Google" pill + a
  left accent border for any google-trigger run; the run detail header shows
  "🔍 Google (SerpAPI)" vs "Standard search".
- Replaced the SerpAPI key (old one near quota; one invalid key rejected, final
  valid key has a fresh 250/month). Keys live only in api/.env (gitignored).

### 2026-06-24 — "Look deeper on a near-name": container ≠ kit + hard price ceiling

Case: "Julldent Zygo Box" (storage box, ₹2399) matched Oralkart "Julldent Zygo
kit" (the full surgical kit that *contains* a box, ₹25,995) — score 0.89. The
names share 2/3 tokens (0.91 weighted), so the near-exact-name override bypassed
the 5x price band (10.8x gap), and a borderline verdict still showed it.

Fix (`structured.py` + `settings.py`): before trusting a strong NAME match, probe
the discriminators a surface match hides —
- **Product KIND**: a CONTAINER word (box/case/stand/holder/…) vs a BUNDLE word
  (kit/set/system/combo/…). One side purely container + other purely bundle +
  price out of band → REJECT (`_kind_mismatch`).
- **Hard price ceiling** (`price_band_hard_ratio=8.0`): a per-unit gap beyond 8x
  can't be a pack/form difference (pack is normalized out); with no corroborating
  spec/attribute it's a different product → REJECT.
Both are uncorroborated-only: an agreeing model-code/size/spec still matches. The
Meril reel-vs-pack near-name override (1.5x, same kind) stays CONFIRMED; Box-vs-
Box, Kit-vs-Set, Kit-vs-plain are untouched. 27/27 regression (added the case).

### 2026-06-24 — Google re-run + Standard-vs-Google comparison view

- **Re-run via Google**: each run row now has "🔍 Google" (re-run the exact same
  products through SerpAPI) alongside "↻ re-run" (standard). `POST
  /runs/{id}/rerun?serp=1` → `execute_run("rerun-google", products, source_run_id,
  use_serp=True)`, capped to 15 products for quota. The re-run stores with
  `source_run_id` = the original.
- **⇄ compare** link on any re-run (has a source_run_id): `compareRuns()` fetches
  the re-run + its source, matches products by name, and renders a side-by-side
  diff table — L = source, R = re-run — with per-competitor cells tinted green
  (R found, L missed) / amber (L found or price differs), plus a header tally
  ("R found +N", "L found +N"). Lets you A/B Standard vs Google on the SAME
  products in the UI. Verified: std #8 vs Google #16 surfaced where each path
  won/lost competitors (3M ESPE: std found oralkart, Google didn't).

### 2026-06-24 — Google toggle on Scheduled Runs + per-run accuracy

- **Google (SerpAPI) toggle on the Scheduled-Runs page** (next to count + Run
  now). ON → "Run now" runs each product through the SerpAPI path (`serp_compare`)
  instead of `_compare_one`, stored as a normal run (trigger `manual-google`) and
  viewable like any other. HARD-CAPPED to 15 products (and a confirm dialog) to
  protect the ~100 searches/month free quota; UI caps the count box to 15 and
  shows a quota note when the toggle is on. `POST /runs/trigger?count=N&serp=1`;
  `execute_run(..., use_serp=True)`. Auto 5×/day runs stay on the standard path.
- **Per-run accuracy now visible**: linked the 50 prior reviews (all `run_id`
  NULL — submitted from the live view) to run #3, which they matched 50/50; it now
  shows 56% in the runs list. Dropped the all-time-only header pill in favour of a
  per-run emerald accuracy pill + a "N reviewed" count. Reviews submitted from an
  opened run detail link to that run going forward (CURRENT_RUN_ID).

### 2026-06-24 — UI: Google/SerpAPI toggle, custom run-size, all-time accuracy visible

- **Google (SerpAPI) toggle** in the Quick-search card. ON → the single search
  hits `/serp/compare` (Google finds each competitor's exact PDP, our matcher
  verifies) instead of `/compare/single`; a "🔍 via Google" badge marks the
  result. Applies to the single search only — the batch upload stays on the
  standard pipeline (SerpAPI free tier = 100 searches/month, so a full sheet
  would exhaust it). (`static/index.html`)
- **Multi-candidate SerpAPI** (`serp.py`/`routes/serp.py`): per source, return ALL
  PDP candidates in Google page order and verify the top 4 through the matcher,
  keeping the best (earlier rank wins ties). No extra SerpAPI quota — candidate
  PDP fetches use our scraper. Spencer Scissors now finds both competitors.
- **Custom run size**: `POST /runs/trigger?count=N` (1–200) overrides the default
  50; `_build_run_products(count)` honours it (watchlist capped to count). UI: a
  "products" number box next to "Run now". (`routes/runs.py`, `scheduler.py`)
- **All-time accuracy visible**: the Scheduled-Runs header now shows "All-time
  accuracy X%" from `/reviews/summary` (persisted across reloads, not just the
  flash after submitting). NB: the existing 50 reviews have `run_id=NULL` (they
  were submitted from the live view), so per-run accuracy still reads "—" until
  reviews are submitted from an opened run detail (which sets CURRENT_RUN_ID).

### 2026-06-24 — WHO-probe deep dive: selective loosening (grouped-child + SKU-code + brand inheritance + SerpAPI display)

Driven by the example "Oracraft Single Ended WHO Screening Probe #3 - PCP11.5B"
and the user's insight that SerpAPI *finds* the right PDP but our strict logic
sometimes *bypasses* it. Diagnosed three distinct failure modes and fixed each on
a broad level (26/26 regression, incl. 4 new WHO-probe cases):

- **A. DK grouped-child resolution.** `fuzz_ratio` is `token_set_ratio`, which is
  "tolerant of extra tokens on either side", so a standalone that is a near-SUBSET
  of the input ("…Probe #3 - EXS6" vs "…WHO Screening Probe #3 - PCP11.5B") reads
  0.928 and blocked the correct grouped child. `_resolve_dk` now also looks for a
  better child when the current match is *missing the input's distinctive tokens*
  (not only when fuzz is low). DK now resolves WHO→₹220, Thin Willam→₹195,
  EXS6→its own standalone — each to its correct distinct product.
  (`routes/compare.py`)
- **C. SKU-tail code discriminator.** `_MODEL_RE` needs ≥2 digits, so 1-digit
  catalog codes (EXS6, POW6, EXD5) were invisible and the two-sided model-code
  gate never fired. Added `_SKU_RE` = 3+ contiguous letters + digit(s) (SKU-like;
  excludes digit-first sizes "15g", 1-letter shades "A3", spaced "No 6", "Pack of
  5", with a small word guard). Now PCP11.5B ≠ EXS6 ≠ EXA6 ≠ POW6 — the wrong
  near-duplicate sibling is gated out. (`matching/attributes.py`)
- **D. Brand inheritance parent→child (broad bug A exposed).** Competitors often
  put the brand only on the PARENT PDP ("Maarc Articulating Paper") while variant
  labels drop it ("Articulating Paper 40μ - Blue & Red"). Selecting a child lost
  the brand and the brand gate wrongly rejected a correct sub-variant. `select_
  variant` now carries the parent brand onto the chosen child. Maarc 40µ Blue&Red:
  base 0.78 → correct sub-variant **0.914**. (`pipeline.py`)
- **B. SerpAPI display floor.** In the isolated `/serp` path, a gate-clean,
  non-REJECTED match from a Google-pinned PDP is now surfaced past the UI's 0.70
  cutoff (true cosine kept in `cosine`). pinkblue's reworded WHO probe (cosine
  0.68) now shows; the wrong EXS6 sibling stays out via the gate. (`routes/serp.py`)

WHO-probe end-to-end now: DK ₹220 ✓ · pinkblue WHO probe shown ✓ · oralkart EXS6
correctly absent ✓. 100-sheet diff: the ±3 movement is live-scrape variance (each
"lost" pinkblue returns ≥0.78 when re-run individually; none are code-gated) plus
a real +1 (Ethicon Mersilk).

NEXT: the user's SerpAPI-intelligence spec — rank candidate PDPs per competitor by
Google page-order, brand-gate first, then name word/char overlap, then serial-code
discrimination (steps 6–8 already covered by the gate/attributes work above),
then sub-variant + packaging/description. Build = collect ALL per-domain SerpAPI
candidates (not just best-slug) and run them through the matcher, preferring
earlier rank on ties.

### 2026-06-23 — Tip-number discriminator + pricing insights + per-product insight & price history

- **Instrument tip number** (`attributes`/`gates`/`structured`). A hand-instrument
  tip/size number is now a hard discriminator: "#6", "No. 3", "- 6", "-1", and a
  bare word-trailing number all map to the same tip — so **"#3" == "3"** but
  **"-1" ≠ "- 6"**. Fixes GDC Endo Spoon Excavator "-1" wrongly matching the
  "- 6" (both share code EXC32L). Pack counts ("of 3"), measurements (mm/g/%),
  and code hyphens (TR-13/DL-300/2-0) are excluded. (Confirmed the scheduler is
  live: a product's history shows the manual run + the 10:00 IST scheduled run.)
- **Pricing insights panel** (UI) over results + run detail: counts of where DK
  is lowest / most-expensive / mid, "above cheapest competitor", total
  raise-headroom, and a top-opportunities table (raise price up to the priciest
  competitor).
- **Per-product insight** (UI): a "View insight" button before each product opens
  a closable inline panel showing (a) that product's pricing position vs each
  competitor + raise-headroom, and (b) its **price history across past runs**
  (`GET /runs/history?name=…`, from the SQLite run store) with a DK trend.
- Regression suite now **20 cases** (adds tip-number + the earlier fixes).


### 2026-06-22 — Scheduled SKU runs (admin catalog API + scheduler + history UI)

New feature: pull random SKUs from DentalKart's catalog and auto-compare them
5×/day, with a browsable history in the UI.

- **Admin catalog client** (`dk_admin.py`). Reverse-engineered the admin SPA: the
  product list is `POST serverless-prod.dentalkart.com/api/v1/products/list/view`
  authenticated by the frontend `x-api-key` (no user login needed). Pulls random
  enabled products and returns **name only** (the pipeline resolves DK + price
  itself); ~46k-product catalog, sampled by random pages. Key in `api/.env`.
- **Scheduler** (`scheduler.py`). Dependency-free asyncio loop — fires at the
  configured IST times (`10:00,11:30,13:00,14:30,16:00`), server-side, for the
  life of the process (always-on host; no run-on-boot, resumes at next slot).
  Each run: random SKUs → `_compare_one` per name (concurrency 4) → persist.
  Off by default; `SCHEDULED_RUNS_ENABLED=1` + key turns it on.
- **SQLite store** (`run_store.py`, `api/data/runs.sqlite3`, gitignored). `runs`
  + `run_items` (full CompareResult JSON per SKU). 30-day retention prune.
- **API** (`routes/runs.py`): `GET /runs`, `GET /runs/{id}`, `POST /runs/trigger`.
- **UI** — new "Scheduled Runs" topbar nav + section, reusing the existing
  card / compare-table / cellHtml / pills so it matches the live-results design
  exactly. Run list → click → the familiar comparison table for that run.
  "Run now" button triggers a manual run. Refactored the table builder into a
  shared `compareTableHTML()` used by both live results and run history.
- Config in `settings.py` (`scheduled_*`, `dk_admin_*`, `runs_*`).
  Deployment to the always-on server (e.g. pc-tool.dentalkart.com) is the only
  remaining handoff step.


### 2026-06-22 — Systemic reliability pass (stop name-centric misses recurring)

**User: "why do these issues come again and again… solve for the bigger picture
so similar issues don't recur."** The recurring symptom (a product exists on a
site but we miss it / show the base name) had ONE underlying cause: the pipeline
trusted the NAME at several stages, and real catalogs break that assumption in
different ways each time. Fixes here are general, not per-case:

1. **DK child-aware resolution when the parent name diverges** (`compare.py`).
   When `_best_match` finds no parent (grouped parent named unlike its child,
   e.g. input "…Suture Corn Pliers - Large" under parent "Julldent Micro Tissue
   …Forcep (JULL-DENT 074)"), it now falls through to `_resolve_by_child_name`
   instead of returning NONE. → resolves ANY grouped product whose children have
   divergent names. (Was: DK NONE for the whole 074 family.)
2. **Description-boosted semantic match** (`structured.py`). Cosine now also
   considers each side's PDP description (bounded slice, MAX with name-cosine so
   it only helps). A terse listing whose identity is in the body
   ("Dental Avenue Avuecal" → desc "Premixed Calcium Hydroxide Paste … syringe")
   now matches. General to any sparse competitor listing.
3. **Terse-listing rescue in top-K** (`pipeline.py`). A candidate sharing a
   specific (≥6-char) token with the input gets a PDP-verify slot even if its
   name triages weak — so the description boost can actually run on it. Capped;
   gates still decide.
4. **Best-signal confidence** (`pipeline.py`). A cell's shown score is
   `max(name-triage, semantic-cosine)`, so a match confirmed via description
   isn't buried below the 0.7 display filter by a low name score.
5. **Brand house-line prefix** (`gates.py`). Brand "Avue" matches the coined
   product word "AvueCal" (oralkart "AvueCal - Calcium Hydroxide…"); ≥4-char
   brands only. Complements the existing alias map.
6. **Near-exact name overrides the price band** (`structured.py`). A strong-name
   match (fuzz ≥ confirm_fuzz or token ≥ 0.6) is the same product even when the
   unit-price band fails — that gap is pack/FORM (a 25 Mtr reel vs a pack, or a
   mis-parsed pack size like "25Mtr"→25), not a different product. → "Meril
   Filasilk #2-0" now matches pinkblue's Filasilk #2-0 (was wrongly losing to an
   in-band "Mericron XL #2-0"). The band still vetoes WEAK-name lookalikes
   ("compressor valve" vs "air compressor").
7. **Regression suite** (`tests/matching/test_regression_cases.py`). 16
   deterministic, network-free assertions locking in EVERY fixed case (gates,
   structured_match, _pick_dk_child, select_variant). New fixes get a case here
   so they can't silently regress. Run: `uv run pytest tests/matching -q`.

Results: AvueCal now matches pinkblue (0.74) + oralkart (0.90); the JULL-DENT 074
family resolves; watch-list unregressed. **Not fixable (DK-side):** needle holder
"(073A/073B)" — DK's `…073.html` is a genuine soft-404 ("Product Not Found"), so
children can't be read; we correctly fall back to the parent name.


### 2026-06-22 — Pinkblue non-standard PDP parsing (bulk-price / variant-table layout)

**User: pinkblue HAS the product (oro-gutta-percha-points-2.html, "Sure Endo
Gutta Percha Points") but it's not in our results.**

Root cause: that page is a NON-standard pinkblue layout — no JSON-LD Product, no
`data-price-amount`, no Magento swatches. The scraper got `price=0` → returned
null → the product silently vanished. Fixes in `lib/scrapers/pinkblue.ts`:
1. **Variant table parsing keyed on the cell**, not `tbody[id^='id_']` (which
   matched nothing here): iterate `td[data-th="Variant Name"]`, walk up to the
   row for name + price. Now yields the 12 size children (#08…#90-140).
2. **Price fallback chain**: JSON-LD → `data-price-amount` → `[data-final-price]`
   (the "main-bulk-price" attr) → cheapest variant price.
Plus a `select_variant` tie-break: when sub-variants tie on input-token hits,
break by name-fuzz so "#15" wins over the "Assorted #15-40" range (mirrors the
DK `_pick_dk_child` fix). → *Sure Endo GP 2% #15 / #50* now match pinkblue and
display "…2% #15" / "…2% #50".

Known remaining (not the parse bug): *#80* ranks pinkblue's "Sure Endo **ProT**
Gutta Percha Points" (ProTaper, a different line) over the correct "#80" child —
a top-K/specialization ranking nuance to address separately.

**Preventing this class of error going forward:**
- Every scraper field should use LAYERED fallbacks (JSON-LD → og/h1 → page
  attrs), never a single selector. (DK + pinkblue now do.)
- PDP-parse failures are already logged by the sidecar (`→ null in Nms`); when a
  product "isn't in results", check `/tmp/sidecar.log` for that URL first.
- The thin search-card (name+price) is the fallback when a PDP won't parse — so
  a product still matches at base level instead of vanishing.
- TODO: a `scripts/check_pdp.py <competitor> <url>` diagnostic that prints which
  extraction layer fired, and a small fixture of known-tricky URLs as a
  regression guard.

### 2026-06-22 — Competitor sub-variant display (default), archwire wire-form/dim discrimination

**User: competitors show the BASE name even when the input names a sub-variant
(Rabbit CIA archwire "Upper 016 X 022"). Make showing the matching sub-variant
the default.**

1. **`select_variant` is now name-aware and always drills in** (`pipeline.py`).
   It previously only ran for captured size specs and deliberately left the
   display name as the base. Now it also pins a child by the input's
   distinguishing tokens (e.g. "upper", "016", "022") and, when the input
   *names* that child (strict name-token winner), rewrites the DISPLAY name to
   the resolved sub-variant. A pure composition-spec match (input didn't name
   the variant) still updates price/spec only and keeps the base name — avoids
   junk labels like GC's "1-1 PKG". (`discover` passes `dk_record.name`.)
   → Rabbit archwire pinkblue: "…Wires (Pack Of 5)" → "…Size 016 X 022 Short
   Upper". Prima bur: pinkblue/oralkart now show the exact ISO-coded variant
   (verified those are REAL competitor variant names, not DK leakage).

2. **3-digit archwire dimensions** (`attributes.py`). `_DIM_INT_RE` was 2-digit
   only; now `\d{2,3}` so "016 X 022" → code "016x022" (≠ "017x025"). Decimal
   ".016 x .022" already normalized the same way, so notations agree.

3. **Wire-form (Upper/Lower) as a discriminator** (`compare.py`,
   `_dk_has_input_product`). DK lists each archwire separately and its search
   returns the nearest sibling; "Lower 016 X 022" was anchoring on DK's only
   stocked "Upper 016 X 022". Now an Upper/Lower mismatch means DK doesn't carry
   the input → competitors match the INPUT → pinkblue pins "…016 X 022 Short
   Lower". (DK correctly shows NO MATCH for sizes it doesn't stock.)

Analysis answered, no code needed: *Sure Endo Gutta Percha 2% #15/#80* — DK
resolves the size correctly; competitors only had a 0.52 "Retraction Cord" (now
hidden by the 0.7 filter) — pinkblue stocks only 4%/6%/ProTaper/non-standardized
(or a broken-PDP listing), oralkart only 4%/6%. So NO competitor match is correct.

Verified watch-list (GC, Maarc 40/70µ, Prima, forceps 041D, GBR base, GP
#50/#80, retractor 079C, suture, archwire Upper/Lower/017×025) — no regressions.

### 2026-06-20 — Base-variant naming, GP size resolution, endo-type gate, 0.7 display filter

From a 100-row `product_names.csv` test. (Note: the UI uses `/compare/batch-stream`,
which streams to the browser and is NOT persisted — re-run to inspect; DK name
resolution is deterministic so it reproduces exactly.)

1. **Base-variant name kept when the input doesn't pin a child** (`compare.py`,
   `_pick_dk_child`). `use_code` checked `bool(in_code)`, so a trailing descriptor
   like "(Pack of 5)" counted as a child discriminator and the resolver drilled
   into an arbitrary length child. Now gated on `_looks_like_code(in_code)`.
   → *Surgident GBR Screw ∅ 1.4mm (Pack of 5)* — DK's grouped parent has 6 length
   children (×3/4/6/8/10/12mm); the input names no length, so we now keep the
   BASE name instead of writing "…∅ 1.4mm **x 3mm (SDS-140-030)**". (This is the
   "sub-variant name written instead of base" report.)

2. **Size child vs range child tie-break** (`_pick_dk_child`). When an input size
   token appears in both an exact child and a range child ("#80" ∈ both "#80" and
   "#45-80"), the token-count tie returned None → dropped to parent. Now breaks
   the tie by name fuzz (exact size wins); range queries still resolve to the
   range. → *Sure Endo Gutta Percha 2% #80* now resolves to "#80" (was "…2%").

3. **Gutta-percha ≠ paper/absorbent points** (`gates.py` category exclusion).
   Endo "points" of different kinds. paper↔absorbent are synonyms (same side).
   → *Sure Endo Gutta Percha 2% #50*: oralkart's "Sure Endo **Paper Points**"
   false match removed. (oralkart only stocks Sure Endo GP **4%/6%**, not the 2%,
   so NO MATCH is correct; a 0.52 "Retraction Cord" borderline is hidden by #4.)

4. **0.7 confidence display filter** (`static/index.html`). Per the request to
   "remove results below overall confidence 0.7": matches scoring < `MIN_CONFIDENCE`
   (0.7, adjustable constant) render as "no match" and are excluded from the
   summary counts. Display-only (underlying data/feedback untouched) and reversible.

All verified against the watch-list (GBR, GP #50/#70/#80/#45-80, forceps 041D,
Korean box kit tier, retractor 079C, Maarc straight+horseshoe codes, GC) — no
regressions.

### 2026-06-20 — Six precision fixes from a live case sweep (forceps/needle/micron/instrument/brand-alias/recall)

Worked through a rapid batch of user-reported wrong results. Each fix is targeted
and verified against the full watch-list (GC, forceps 041D, retractor 079C, Maarc
×3, micro tissue, suture, Upcera) with **no regressions**.

1. **Same-code child disambiguation** (`compare.py:_resolve_by_code`). DK grouped
   products can have TWO children sharing one parenthetical code (data quirk:
   both "Micro Forcep Tooth - Angled (041D)" and "Diamond Dusted … Angled 45
   (041D)"). The code pass returned the FIRST code match; now it collects ALL
   code matches and picks the best **name** fuzz to the input.
   → *Julldent Diamond Dusted Micro Surgical Forceps - Angled 45 (041D)* now correct.

2. **DK soft-404 guard + JSON-LD-absent fallback** (`lib/scrapers/dentalkart.ts`).
   (a) Some grouped products ship NO JSON-LD Product node — `fetchDentalkartProduct`
   used to bail; now it recovers the name from `og:title`/`<h1>`/`<title>` and
   parses children from the RSC. (b) DK serves a soft-404 ("Product Not Found",
   HTTP 200) for delisted/broken products and redirects to the child slug; its
   "related products" carousel would be mis-parsed as children — added a title
   guard so we never surface that junk.
   → *Julldent Micro Surgical Needle Holder … (073A)*: DK's child PDP is currently
   a genuine soft-404 (verified 3/3, both clients), so the tool correctly falls
   back to the parent name from search. **DK-side data issue**, not a tool bug.

3. **Micron thickness as a hard discriminator** (`attributes.py`). "70 Microns",
   "40µ Microns" → model_code `70u`/`40u`; the two-sided code gate rejects 40µ↔70µ.
   → *Maarc Articulating Paper 70µ Horseshoe (5533/050)* no longer falsely matches
   oralkart's **40µ** horseshoe; the 40µ variant still matches 40µ correctly.

4. **More incompatible instrument types** (`gates.py`). Added drill(s), needle,
   holder, knife, chisel to the instrument group (gate rejects only when BOTH
   sides carry a *disjoint* instrument noun, so correct matches are untouched).
   → *Julldent Micro Tissue Forceps - Straight Tooth*: oralkart's false
   "Julldent Tissue Punch **Drills**" match is gone (now correctly NO MATCH —
   no competitor actually carries this product).

5. **Same-manufacturer brand alias** (`gates.py`). Tiny explicit map
   (`avue → dental avenue`). "Avue" is Dental Avenue's house line; pinkblue lists
   it as "Dental Avenue Avuecal". Brand discipline preserved (not a cross-brand
   match). Control: avue↔unrelated still rejects.

6. **Query-builder recall** (`query_builder.py`). Product-line words that merely
   *start with* the brand were dropped (substring test), so "AvueCal" (brand
   "Avue") was lost and the query competitors index by was never generated.
   Now only the exact brand word is dropped → query "Avue AvueCal" is emitted.

7. **Candidate dedup by canonical URL + larger PDP top-K** (`pipeline.py`,
   `settings.py`). `scrape_all_queries` deduped by FULL URL, but oralkart (Shopify)
   returns the same product with per-query search-tracking params
   (`?_pos=2&_psq=…`), so 3 copies of one product ate all `pdp_top_k=3` PDP slots
   and pushed the correct sub-variant out before it was ever evaluated. Now dedup
   by URL path (query/fragment stripped); bumped `pdp_top_k` 3→5 (PDPs fetch
   concurrently, so latency barely moves).
   → *Prima Dental Diamond Bur 856-018M (TR-13)*: oralkart's
   "Prima Dental Tapered Round Diamond Bur **TR Series**" (TR = Tapered Round)
   was in the pool + triaged confirmed but never PDP-fetched (3 duplicate
   "Endo Access Diamond Bur" cards out-ranked it). Now correctly matched.

8. **"No shared distinctive token" gate** (`gates.py`). Two same-brand products
   that share ONLY the brand + a generic format noun (tray/paper/kit/box/pack/
   bottle/syringe…), while each carries its own distinctive token, are different
   products. Conservative: never fires if either side is purely generic (a terse
   "Maarc Articulating Paper" base listing still matches). Shape/material words
   (diamond, straight, niti…) are NOT treated as generic.
   → *Maarc Dental Tray **Adeziv** With Thinner* (a tray ADHESIVE) no longer
   false-matches "Maarc **Eazy Tray**" (an impression tray) on pinkblue/oralkart
   — no competitor actually stocks the adhesive, so NO MATCH is correct.
   Verified it does NOT break AvueCal, GC, Angelus, Prima bur, or the knives.
   Note on the Maarc papers: pinkblue lists only a generic "Maarc Articulating
   Paper" (no micron) → surfaced as [possible]; oralkart's "40µ & 100µ combo"
   matches the 40µ/100µ inputs ([possible], it contains them) and 70µ correctly
   gets NO MATCH (micron gate). Horseshoe 40µ is [confirmed].

**Still open after this sweep (need bigger changes, not shipped unvalidated):**
- *Avue AvueCal* — candidate now reaches the matcher, but triage REJECTS it: the
  pinkblue search-result name "Dental Avenue Avuecal" is lexically far from the
  input; the matching detail ("Premixed Calcium Hydroxide") lives only in the
  pinkblue **PDP description**. Needs **competitor-PDP enrichment** for borderline
  candidates (fetch PDP, re-match on description) — a hot-path change.
- *Angelus Interlig Single Patient Strip* — pinkblue has the correct
  "Angelus Interlig (4832)" ₹3324 (≈DK ₹3800) AND a cheaper "Single Piece" ₹1181;
  the tool picks "Single Piece" (misleading shared token "Single", and DK's
  "Pack of 4" pack-normalization makes the single piece look in-band). Needs
  **pack-aware / DK-price-anchored disambiguation** among competitor siblings.

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

### 2026-06-23 — Human accuracy review (per-row ✓/✗ + notes → accuracy + logs)

- Per-result-row review controls: ✓ (reviewed-correct) or ✗ (needs-fix → reveals
  an improvement note box). A submit bar computes **accuracy = correct/reviewed**
  and shows this-batch + all-time figures.
- Backend `routes/reviews.py` + `reviews` table in the run-store SQLite:
  `POST /reviews` (store batch, return accuracy), `GET /reviews[?only_issues=1]`,
  `GET /reviews/summary`. Every needs-fix is also emitted as a `review-issue` log
  line (product + dk_matched + message) so improvement notes are easy to pull.
- Workflow: grep `review-issue` (or GET /reviews?only_issues=1) → diagnose →
  fix at the layer where info is lost → add a regression case (same durable loop).

### 2026-06-23 — Watchlist (45 random + 5 fixed), re-run a past run, per-run accuracy

- **Fixed watchlist**: each scheduled run now = 5 FIXED products (seeded once from
  random, then constant) + 45 fresh random — so the price-history/comparison
  feature has a continuous series. Capped + self-healing (`run_store.watchlist`,
  `scheduled_watchlist_size`). `GET /runs/watchlist`.
- **Re-run a past run**: `POST /runs/{id}/rerun` replays the EXACT products of a
  past run (trigger=rerun, source_run_id) so prices can be recomputed and
  compared. UI: "↻ re-run" per row; re-runs show "(re-run of #N)".
- **Per-run accuracy**: reviews now carry `run_id`; the runs list + detail show
  each run's accuracy (correct/reviewed), so accuracy can be tracked/compared
  across runs over time.

### 2026-06-23 — SerpAPI discovery (isolated, opt-in) + first evaluation

Built a SEPARATE Google/SerpAPI discovery path that does NOT touch /compare:
- `app/serp.py`: one `google` search (using the BASE name so long titles still
  match) → segregate organic results by `source`/domain → keep only real PRODUCT
  pages (category/collection/brand listing pages filtered out) → pick the link
  whose slug best matches the product. `site:<domain>` fallback for competitors
  missing from the broad results.
- `routes/serp.py`: `GET /serp/compare?name=` reuses our DK resolver + matcher
  (select_variant + structured_match) and returns the SAME CompareResult shape,
  so the existing UI could render it. `GET /serp/urls` for debugging.
- Config: `serpapi_key`, `serp_enabled` (default off), `serp_site_fallback`.
  Key in api/.env (gitignored). SerpAPI free tier = 100 searches/month.

Finding (6-product A/B incl. long/generic names): SerpAPI returns clean real PDP
links by source AND genuinely found a product the tool missed earlier (WHO
probe). BUT on this sample the CURRENT TOOL ≥ SerpAPI: ties on 4, tool wins on 2
(long descriptive names — SerpAPI still missed pinkblue). The brand/recall fixes
already gave the tool strong coverage. SerpAPI is parked behind the flag (no UI
wired yet) pending a larger A/B if we want broader-merchant coverage later.

NEXT (tomorrow): decide whether to A/B SerpAPI on a bigger sheet via the review
loop, or leave it parked; remaining review themes — cross-competitor variant
alignment (generic names: pick the variant common to all competitors).
