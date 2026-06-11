# product-compare

Python FastAPI service that compares Dentalkart product prices against
competitor sites (Pinkblue, Oralkart, Dentmark) using sentence-transformer
embeddings + heuristic gates + rapidfuzz for matching.

## Architecture

```
Browser → http://localhost:8000 (FastAPI / Python)
              │  matching engine: search → triage → PDP fetch → structured
              │  attribute match → LLM judge (borderline) → match registry
              ▼
          http://127.0.0.1:3100 (Node sidecar)
              │  hosts the TS scraper modules in lib/scrapers/*.ts
              ▼
          pinkblue.in / oralkart.com / dentmark.com / dentalkart.com
```

* **`api/`** — Python service. FastAPI + asyncpg + sentence-transformers
  + rapidfuzz. Owns matching, UI, orchestration, DB schema.
* **`lib/scrapers/`** — TypeScript HTTP/HTML scrapers. One module per
  competitor. Exposed to Python via the Node sidecar.
* **`api/bridges/scrape-server.ts`** — Node sidecar that loads every
  scraper once and serves them over `localhost:3100`
  (`GET /:competitorId?q=` for search, `GET /product?scraper=&url=`
  for a single PDP).

## Running locally

You need two processes side by side.

### 1. Node scraper sidecar (terminal 1)

```bash
npm install        # one-off
npm run scrape-server
# → http://127.0.0.1:3100/health  →  { "status": "ok", "scrapers": [...] }
```

### 2. FastAPI service (terminal 2)

```bash
cd api
uv sync            # one-off
uv run alembic upgrade head   # one-off: create/upgrade tables
uv run uvicorn app.main:app --port 8000 --reload
# → http://localhost:8000/        — drag-and-drop tester UI
# → http://localhost:8000/docs    — OpenAPI explorer
# → http://localhost:8000/health
```

Drop an Excel with a `Product Name` column on the homepage. For each row
we search dentalkart.com plus the configured competitors, match every
returned candidate with the Python pipeline, and show the best price.

## Tests

```bash
cd api
uv run pytest -v
```

122 tests covering normalize, attributes, gates, scoring, triage, token
similarity, query builder, schemas, /match route, and a 20-case TS-parity
fixture.

## Match registry & golden set

First run for a product does full discovery (search + PDP fetch + structured
match + LLM judge for borderline pairs) and stores the verified link in
`product_links`. Later runs just re-scrape the stored URLs for fresh prices.
👍 permanently verifies a link, 👎 kills it. ⭐ saves a golden-truth label
(∅ on an empty cell = "no match exists"); measure accuracy with:

```bash
cd api
uv run python scripts/eval.py
```

Set `ANTHROPIC_API_KEY` in `.env` to enable the LLM judge (Claude Haiku,
budget-capped per run). Without it the pipeline runs rules-only and
borderline pairs stay POSSIBLE.

## Docs

* `docs/superpowers/specs/2026-05-13-python-matching-backend-design.md`
* `docs/superpowers/plans/2026-05-13-python-matching-foundation.md`
