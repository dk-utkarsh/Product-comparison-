# Python NLP/ML Backend for Product Matching & Comparison

**Status:** Draft — pending user approval
**Author:** automation1@dentalkart.com
**Date:** 2026-05-13

## 1. Goal

Replace the current Node/TypeScript matching, scraping, DB, and cron layers of `updated-product-compare` with a Python FastAPI service that uses NLP and ML for product matching. Keep Next.js / React as the UI shell only. The Python service owns all business logic.

## 2. Why

The current TypeScript matcher relies on `string-similarity.compareTwoStrings` and ~16 hand-coded regex conflict checks. It struggles with semantic variation ("GC Fuji IX GP Extra" vs "GC FujiIX GP Capsules"), with multi-language product names, and is brittle to add new conflict rules. We have a Dentalkart product-name corpus we can use to build a proper embedding-based matcher. NLP + ML gives us a higher accuracy ceiling and a learnable system instead of a regex pile.

## 3. Non-goals

- Re-architecting the React UI (it stays as-is; only its API client changes).
- Replacing Postgres or the existing schema (we extend it, we don't rewrite it).
- Building a labeled-pair training pipeline now — that's a follow-up.
- Big-bang cutover — we strangle, scraper by scraper.

## 4. Scope

Moves to Python (FastAPI):

- Matching: `normalize`, `matcher`, `smart-matcher`, `keyword-extractor`, `variant-extractor`, `pack-detector`, `match-triage`.
- Scrapers: all 18 site scrapers in `lib/scrapers/` plus `web-discovery` and `web-search` (Startpage / DuckDuckGo / Google).
- Persistence: `lib/db.ts` and all Postgres reads/writes (`competitor_url_cache`, `monitored_products`, `custom_urls`, `comparison_results`, `price_history`, `cron_runs`).
- Scheduling: `scripts/cron.ts` and `lib/monitor-worker.ts`.
- Excel parsing for upload endpoints.

Stays in Next.js / React:

- All `app/` UI routes (`/compare`, `/compare-tool`, `/dashboard`, `/monitor`).
- All `components/`.
- `app/api/*` endpoints become thin proxies that forward to the Python service. They handle: auth (if any), request validation, and forwarding. No business logic.

## 5. Architecture

### 5.1 Process model

Two processes during dev and prod:

- **Next.js** on `:3000` — UI + thin API proxy.
- **Python FastAPI** on `:8000` — all matching, scraping, DB, cron.

Both share one Postgres. Docker Compose runs all three locally.

### 5.2 Communication

- React calls Next.js API routes (existing endpoints, unchanged from the UI's perspective).
- Next.js API routes forward to FastAPI over HTTP using `fetch` to `http://api:8000` in Docker, or `http://localhost:8000` when running natively.
- Request/response JSON shapes mirror today's TypeScript types. Pydantic models in Python are the source of truth; TS types are re-derived as a thin client.

### 5.3 Repo layout

```
updated-product-compare/
├── app/                  # Next.js UI (unchanged structure)
├── components/           # React (unchanged)
├── lib/                  # shrinks to a typed API client for the Python service
│   └── api-client.ts     # NEW — single source of HTTP calls to FastAPI
├── api/                  # NEW — Python service
│   ├── pyproject.toml
│   ├── Dockerfile
│   ├── app/
│   │   ├── main.py
│   │   ├── settings.py           # pydantic-settings for env
│   │   ├── routes/
│   │   │   ├── compare.py        # POST /compare        (was /api/scrape)
│   │   │   ├── upload.py         # POST /upload, /upload-compare
│   │   │   ├── export.py         # POST /export, /export-compare
│   │   │   ├── monitor.py        # /monitor/*
│   │   │   ├── dashboard.py      # /dashboard/*
│   │   │   └── match.py          # POST /match (new, low-level)
│   │   ├── matching/
│   │   │   ├── normalize.py      # SKU/pack/noise stripping
│   │   │   ├── embed.py          # sentence-transformers wrapper, cached
│   │   │   ├── index.py          # FAISS index lifecycle (build, search, persist)
│   │   │   ├── attributes.py     # regex + (later) spaCy NER for brand/size/pack/shade/...
│   │   │   ├── gates.py          # hard-conflict checks (incompatible groups, etc.)
│   │   │   ├── score.py          # weighted final score + verdict mapping
│   │   │   └── triage.py         # accept/grey/reject orchestration
│   │   ├── scrapers/
│   │   │   ├── base.py           # Scraper protocol + shared httpx client
│   │   │   ├── dentalkart.py
│   │   │   ├── pinkblue.py
│   │   │   ├── ...               # one module per site (18 total)
│   │   │   ├── web_search.py     # Startpage / DDG / Google
│   │   │   └── web_discovery.py
│   │   ├── db.py                 # asyncpg pool + typed query helpers
│   │   ├── cron.py               # APScheduler bootstrap
│   │   └── workers/
│   │       └── monitor.py        # monitoring job (was monitor-worker.ts)
│   ├── scripts/
│   │   ├── build_catalog_index.py  # ingest Dentalkart catalog → embed → save FAISS
│   │   └── run_compare.py
│   └── tests/
│       ├── unit/                 # normalize, gates, score
│       ├── matching/             # golden pairs from current TS output
│       └── scrapers/             # vcr-py recorded HTTP for each site
├── docker-compose.yml
└── .env / .env.example
```

### 5.4 Matching engine

Pipeline for a single (search_term, candidate_name) pair:

1. **Normalize** both (`normalize.py`): lowercase, strip SKU tails, pack suffixes, noise. Same rules as `lib/normalize.ts`, ported.
2. **Embed** both (`embed.py`): `sentence-transformers/all-MiniLM-L6-v2`, 384-dim. Model loads once at startup, embeddings cached in-process LRU + persisted to Postgres for catalog products.
3. **Recall** (only for the "find on Dentalkart" step): query a **FAISS HNSW index** of the pre-embedded Dentalkart catalog → top-50 candidates. For competitor candidates, we receive them from scrapers — no index needed.
4. **Extract attributes** (`attributes.py`): brand, model number, ISO size, shade, concentration, slot, taper, pack count, viscosity, material. Regex first (port from TS); spaCy NER added later when we have labels.
5. **Hard gates** (`gates.py`): reject on brand mismatch, incompatible-group mismatch (instrument vs material, refill vs kit, etc.), ISO size mismatch, shade mismatch, concentration mismatch. Same conflict table as today's `smart-matcher.ts`, ported.
6. **Score** (`score.py`):
   ```
   score = 0.6 × cosine_sim
         + 0.2 × brand_match            (0 or 1)
         + 0.1 × pack_match              (0..1, tolerance 2%)
         + 0.1 × attribute_match         (mean of matched attribute features)
   ```
   Weights live in `settings.py` so they can be tuned without code changes.
7. **Verdict** (`triage.py`): map score + gate results to `confirmed | possible | variant | rejected` using thresholds (`ACCEPT=0.75`, `POSSIBLE=0.55`, `VARIANT=0.45`, else `rejected`). Today's TS thresholds are reused only as starting points — they'll be retuned on the same evaluation set.

### 5.5 DSA choices

| Concern | Structure | Reason |
|---|---|---|
| Brand → product bucket | `dict[str, list[ProductId]]` | O(1) brand bucket lookup before ANN to cut candidate set |
| Stopwords / noise | `frozenset[str]` at module load | O(1) membership in tight loops |
| ANN over catalog | FAISS HNSW (`IndexHNSWFlat`) | Sub-millisecond top-K for tens of thousands of products |
| Top-K matches | `heapq.nlargest` | O(N log K), avoids full sort |
| Incompatible-group lookup | `dict[str, int]` (word → group_idx) | Same shape as today's `WORD_TO_GROUP` |
| Embedding cache | `functools.lru_cache` (in-process) + Postgres `pgvector` column (durable) | Avoid re-encoding the catalog on every restart |
| Scrape candidate dedup | `set[(host, path)]` | Same as today's web-discovery dedup |

### 5.6 Scrapers

- One module per site, exposing `async def search(term: str, ctx: ScrapeContext) -> list[ProductData]`.
- Shared `httpx.AsyncClient` with retries, timeouts, user-agent rotation (ported from `lib/http.ts`).
- HTML parsing with `selectolax` (much faster than BeautifulSoup, similar API to cheerio).
- Web discovery uses `asyncio.gather` over Startpage + DuckDuckGo + Google, same dedup and filter rules.
- Recorded HTTP fixtures (`vcrpy`) make scraper tests deterministic.

### 5.7 Database

- Same Postgres. Add `pgvector` extension.
- New table:
  ```sql
  create table dentalkart_catalog (
    id            bigserial primary key,
    sku           text,
    name          text not null,
    normalized    text not null,
    brand         text,
    embedding     vector(384) not null,
    updated_at    timestamptz not null default now()
  );
  create index on dentalkart_catalog using hnsw (embedding vector_cosine_ops);
  create index on dentalkart_catalog (brand);
  ```
- Existing tables (`competitor_url_cache`, `monitored_products`, `custom_urls`, `comparison_results`, `price_history`, `cron_runs`) keep their current schema. Python reads/writes via `asyncpg`.
- Migrations live in `api/migrations/` (Alembic).

### 5.8 Scheduling

- `APScheduler` with the AsyncIO executor inside the FastAPI process.
- Same cron expression as today (daily 06:00 IST). Job calls the monitoring worker.
- `/monitor/run-now` endpoint triggers an ad-hoc run.
- Job runs are recorded in `cron_runs` (existing table).

### 5.9 Configuration

Single `.env` at the repo root, loaded by both Next.js and the Python service:

```
DATABASE_URL=postgres://...
PYTHON_API_URL=http://localhost:8000        # Next.js → Python
EMBED_MODEL=sentence-transformers/all-MiniLM-L6-v2
EMBED_DEVICE=cpu                            # cuda / mps later
FAISS_INDEX_PATH=./data/catalog.faiss
ACCEPT_THRESHOLD=0.75
POSSIBLE_THRESHOLD=0.55
VARIANT_THRESHOLD=0.45
SCORE_W_COSINE=0.6
SCORE_W_BRAND=0.2
SCORE_W_PACK=0.1
SCORE_W_ATTR=0.1
SCRAPE_CONCURRENCY=10
```

## 6. Data flow

### 6.1 Upload + compare (today's `/api/scrape`)

1. User uploads Excel in React → POST `/api/upload-compare` (Next.js).
2. Next.js forwards file bytes to FastAPI `POST /upload-compare` → returns parsed `{products, columns}`.
3. React shows the parsed list; on submit, calls `POST /api/scrape` per product (existing behaviour).
4. Next.js proxies to FastAPI `POST /compare`.
5. FastAPI:
   a. Search Dentalkart via `scrapers/dentalkart.py`.
   b. Fan out to competitor scrapers in parallel (`asyncio.gather`).
   c. For each competitor's candidate list, run the matching pipeline against the search term.
   d. In parallel, run `web_discovery` to find sellers not in the curated competitor list.
   e. Build `ComparisonResult` (same shape as today) and return.

### 6.2 Daily monitoring

`APScheduler` fires `workers/monitor.py` at 06:00 IST → loads `monitored_products` → runs `/compare` per product → writes to `comparison_results` and `price_history`. Same shape as today's `monitor-worker.ts`.

## 7. Migration plan

Strangler, not big-bang:

1. **Skeleton + index** — Stand up FastAPI, `/health`, Postgres connection, `pgvector` extension. Build the Dentalkart catalog index via `scripts/build_catalog_index.py`. The script accepts either a CSV/Excel export of product names (the corpus the user already has) or pulls live from Dentalkart's `apis.dentalkart.com` search API — whichever is available. The chosen input path is documented in the script's `--help`.
2. **`/match` endpoint** — Port `normalize`, `attributes`, `gates`, `score`, `triage`. Accept `(search_term, candidates: list[str])`, return ranked verdicts. Verify against TS output on a fixed set of (search, candidate) pairs.
3. **First scraper port** — Dentalkart. Side-by-side test: TS vs Python output on 50 product names.
4. **Remaining scrapers** — one PR per scraper. Each PR includes a vcrpy fixture and a parity test.
5. **Web discovery** — port `web-discovery` and `web-search` together.
6. **`/compare` endpoint** — orchestrate scrapers + matching, return `ComparisonResult`.
7. **Next.js proxy switch** — flip `app/api/scrape/route.ts` to forward to Python. Keep the TS path under a feature flag for one release.
8. **DB writes** — move `competitor_url_cache` writes and reads to Python.
9. **Monitor + cron** — port `monitor-worker` + cron, retire `scripts/cron.ts`.
10. **Cleanup** — delete `lib/matcher.ts`, `lib/smart-matcher.ts`, `lib/scrapers/`, `scripts/cron.ts`, etc. once everything is verified.

Each step ships independently. At any point, the app runs.

## 8. Testing

- **Unit tests** — `normalize`, `gates`, `score`, attribute extractors. Pure functions, fast.
- **Matching golden tests** — fixed corpus of (search_term, candidate, expected_verdict) tuples derived from today's TS output for known queries. Acts as a regression net during the port.
- **Scraper tests** — `vcrpy` records real HTTP once, replays in CI. One file per scraper.
- **End-to-end** — Docker Compose spins up Postgres + FastAPI, hits `/compare` with a known product, asserts shape and key fields.
- **Type safety** — Pydantic models on every route. `mypy --strict` on `api/app/`.

## 9. Error handling

- Each scraper wrapped in try/except → returns `[]` on failure. One slow site does not block the batch (`asyncio.gather(..., return_exceptions=True)`).
- Per-scraper timeout (default 15s) and retry (2x with backoff) in `scrapers/base.py`.
- Embedding service failure → 503 from `/match` and `/compare`. UI shows toast.
- DB connection lost → asyncpg pool reconnects; queries retry once.
- Bad input on `/match` (empty term, empty candidates) → 400 with reason.

## 10. Observability

- Structured JSON logs (`structlog`) with request id, scraper name, latency, candidate count.
- Per-route Prometheus metrics (request count, latency, error rate) on `/metrics`.
- Cron run history in `cron_runs` (existing).

## 11. Open questions

- **Embedding model upgrade path.** Start with `all-MiniLM-L6-v2`. If recall on Indian-language or branded product names is weak, evaluate `paraphrase-multilingual-MiniLM-L12-v2` or fine-tune on the Dentalkart corpus.
- **NER labels.** Out of scope for this spec, but worth a follow-up: build a ~500-pair labeled set for fine-tuning attribute extraction.
- **Auth.** The current Next.js endpoints have no auth. The Python service inherits that. If auth is needed later, add a shared bearer token between Next.js and FastAPI.

## 12. Risks

- **Embedding cold start.** Loading `all-MiniLM-L6-v2` takes a few seconds. Mitigate: load at startup, not per request.
- **Scraper drift.** Sites change HTML. Mitigated by vcrpy + golden parity tests; new fixtures captured on failure.
- **Threshold tuning.** Initial thresholds are guesses; we'll retune on a held-out set drawn from today's TS verdicts.
- **Operational complexity.** Two processes instead of one. Mitigated by Docker Compose + a single `make dev` target.

## 13. Acceptance criteria

- `make dev` starts Postgres + FastAPI + Next.js. Visiting `http://localhost:3000` and uploading the existing `test-20-products.xlsx` returns a `ComparisonResult` for every row.
- `POST /api/scrape` matches today's TS output on ≥ 90% of a 100-row regression set (verdict and competitor URLs).
- Daily cron runs and writes to `comparison_results` / `price_history`.
- `lib/matcher.ts`, `lib/smart-matcher.ts`, `lib/scrapers/`, `scripts/cron.ts` are deleted at the end of the migration.
