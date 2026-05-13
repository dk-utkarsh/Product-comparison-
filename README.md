# product-compare

Python FastAPI service that compares Dentalkart product prices against
competitor sites (Pinkblue, Oralkart, Dentmark) using sentence-transformer
embeddings + heuristic gates + rapidfuzz for matching.

## Architecture

```
Browser → http://localhost:8000 (FastAPI / Python)
              │  matching engine: normalize → gates → embed → score → verdict
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
  scraper once and serves them over `localhost:3100`.

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

90+ tests covering normalize, attributes, gates, scoring, triage, token
similarity, query builder, schemas, /match route, and a 20-case TS-parity
fixture.

## Docs

* `docs/superpowers/specs/2026-05-13-python-matching-backend-design.md`
* `docs/superpowers/plans/2026-05-13-python-matching-foundation.md`
