# Python Matching Foundation — Implementation Plan (Plan 1 of 5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a FastAPI service at `api/` with a working `/match` endpoint that ports today's normalize + smart-matcher + triage to Python, backed by sentence-transformer embeddings and a Dentalkart catalog index in Postgres (pgvector).

**Architecture:** New `api/` subdirectory inside the existing repo. Python 3.12 managed by `uv`. FastAPI on `:8000`, asyncpg against the existing Neon Postgres `DATABASE_URL` (added pgvector extension), FAISS-free vector search via `pgvector` for now (FAISS deferred until catalog size requires it). Module boundaries follow the spec: `matching/{normalize,attributes,gates,score,triage,embed,index}.py`. Pydantic models for all I/O. pytest for tests, ruff for lint.

**Tech Stack:** Python 3.12, uv, FastAPI, Pydantic v2, asyncpg, alembic, pgvector, sentence-transformers (`all-MiniLM-L6-v2`), rapidfuzz, structlog, pytest, ruff.

**Spec reference:** `docs/superpowers/specs/2026-05-13-python-matching-backend-design.md` §5.3, §5.4, §5.5, §5.7, §5.9, and migration steps 1–2.

---

## Section A: Project bootstrap

### Task A1: Install uv and Python 3.12

**Files:**
- Modify (run-once setup, no repo files changed)

- [ ] **Step 1: Install uv**

Run:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
exec $SHELL -l
uv --version
```
Expected: `uv 0.x.x` printed.

- [ ] **Step 2: Pin Python 3.12 in a new api/ folder**

Run:
```bash
mkdir -p /Users/maclapctp85/Desktop/updated-product-compare/api
cd /Users/maclapctp85/Desktop/updated-product-compare/api
uv python install 3.12
uv python pin 3.12
```
Expected: `.python-version` file with `3.12` in `api/`.

- [ ] **Step 3: Commit**

```bash
git add api/.python-version
git commit -m "chore(api): pin Python 3.12 for FastAPI service"
```

---

### Task A2: Initialise pyproject.toml with dependencies

**Files:**
- Create: `api/pyproject.toml`
- Create: `api/README.md`

- [ ] **Step 1: Create pyproject.toml**

Write `api/pyproject.toml`:
```toml
[project]
name = "product-compare-api"
version = "0.1.0"
description = "FastAPI service for product matching, scraping, and comparison."
readme = "README.md"
requires-python = ">=3.12,<3.13"
dependencies = [
  "fastapi[standard]>=0.115",
  "pydantic>=2.9",
  "pydantic-settings>=2.6",
  "asyncpg>=0.30",
  "pgvector>=0.3.6",
  "alembic>=1.13",
  "sqlalchemy>=2.0",
  "sentence-transformers>=3.2",
  "rapidfuzz>=3.10",
  "structlog>=24.4",
  "httpx>=0.27",
  "uvicorn[standard]>=0.32",
]

[dependency-groups]
dev = [
  "pytest>=8.3",
  "pytest-asyncio>=0.24",
  "pytest-cov>=5.0",
  "ruff>=0.7",
  "mypy>=1.13",
  "httpx>=0.27",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "RUF"]

[tool.pytest.ini_options]
pythonpath = ["."]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.mypy]
python_version = "3.12"
strict = true
```

- [ ] **Step 2: Create api/README.md**

Write `api/README.md`:
```markdown
# product-compare-api

FastAPI service for product matching, scraping, and comparison.
See `docs/superpowers/specs/2026-05-13-python-matching-backend-design.md`.

## Dev

    cd api
    uv sync
    uv run uvicorn app.main:app --reload --port 8000
```

- [ ] **Step 3: Sync deps**

Run: `cd api && uv sync`
Expected: `uv.lock` created, `.venv/` created, deps resolved.

- [ ] **Step 4: Commit**

```bash
git add api/pyproject.toml api/uv.lock api/README.md
git commit -m "chore(api): scaffold pyproject with FastAPI + ML deps"
```

---

### Task A3: FastAPI skeleton with /health

**Files:**
- Create: `api/app/__init__.py` (empty)
- Create: `api/app/main.py`
- Create: `api/tests/__init__.py` (empty)
- Create: `api/tests/test_health.py`

- [ ] **Step 1: Write the failing test**

Write `api/tests/test_health.py`:
```python
from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_ok():
    client = TestClient(app)
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
```

- [ ] **Step 2: Create empty __init__ files**

```bash
mkdir -p api/app api/tests
touch api/app/__init__.py api/tests/__init__.py
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd api && uv run pytest tests/test_health.py -v`
Expected: FAIL with `ModuleNotFoundError: app.main`.

- [ ] **Step 4: Implement minimal app.main**

Write `api/app/main.py`:
```python
from fastapi import FastAPI

app = FastAPI(title="product-compare-api", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd api && uv run pytest tests/test_health.py -v`
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add api/app/__init__.py api/app/main.py api/tests/__init__.py api/tests/test_health.py
git commit -m "feat(api): FastAPI skeleton with /health endpoint"
```

---

### Task A4: Settings module loaded from .env

**Files:**
- Create: `api/app/settings.py`
- Modify: `api/app/main.py`
- Create: `api/tests/test_settings.py`
- Modify: `.env.example` (add Python service env keys)

- [ ] **Step 1: Write the failing test**

Write `api/tests/test_settings.py`:
```python
import os

from app.settings import Settings


def test_settings_reads_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://test")
    monkeypatch.setenv("EMBED_MODEL", "test-model")
    s = Settings()
    assert s.database_url == "postgres://test"
    assert s.embed_model == "test-model"


def test_settings_score_weights_default():
    s = Settings(database_url="postgres://x")
    assert s.score_w_cosine == 0.6
    assert s.score_w_brand == 0.2
    assert s.score_w_pack == 0.1
    assert s.score_w_attr == 0.1


def test_settings_thresholds_default():
    s = Settings(database_url="postgres://x")
    assert s.accept_threshold == 0.75
    assert s.possible_threshold == 0.55
    assert s.variant_threshold == 0.45
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd api && uv run pytest tests/test_settings.py -v`
Expected: FAIL with `ModuleNotFoundError: app.settings`.

- [ ] **Step 3: Implement settings.py**

Write `api/app/settings.py`:
```python
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str
    embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embed_device: str = "cpu"

    accept_threshold: float = 0.75
    possible_threshold: float = 0.55
    variant_threshold: float = 0.45

    score_w_cosine: float = Field(default=0.6)
    score_w_brand: float = Field(default=0.2)
    score_w_pack: float = Field(default=0.1)
    score_w_attr: float = Field(default=0.1)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd api && uv run pytest tests/test_settings.py -v`
Expected: 3 passed.

- [ ] **Step 5: Append env keys to .env.example**

Append to `/Users/maclapctp85/Desktop/updated-product-compare/.env.example`:
```
# Python API service
EMBED_MODEL=sentence-transformers/all-MiniLM-L6-v2
EMBED_DEVICE=cpu
ACCEPT_THRESHOLD=0.75
POSSIBLE_THRESHOLD=0.55
VARIANT_THRESHOLD=0.45
SCORE_W_COSINE=0.6
SCORE_W_BRAND=0.2
SCORE_W_PACK=0.1
SCORE_W_ATTR=0.1
PYTHON_API_URL=http://localhost:8000
```

- [ ] **Step 6: Commit**

```bash
git add api/app/settings.py api/tests/test_settings.py .env.example
git commit -m "feat(api): pydantic-settings module with thresholds and weights"
```

---

## Section B: Database

### Task B1: asyncpg pool + DB module

**Files:**
- Create: `api/app/db.py`
- Create: `api/tests/test_db.py`

- [ ] **Step 1: Write the failing test**

Write `api/tests/test_db.py`:
```python
import pytest

from app.db import Database, get_db


@pytest.mark.asyncio
async def test_db_returns_one_row():
    db = await get_db()
    rows = await db.fetch("SELECT 1 AS one")
    assert rows[0]["one"] == 1
    await db.close()


@pytest.mark.asyncio
async def test_db_fetchrow():
    db = await get_db()
    row = await db.fetchrow("SELECT 2 AS two")
    assert row is not None
    assert row["two"] == 2
    await db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd api && uv run pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: app.db`.

- [ ] **Step 3: Implement db.py**

Write `api/app/db.py`:
```python
from __future__ import annotations

from typing import Any

import asyncpg

from app.settings import get_settings


class Database:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @property
    def pool(self) -> asyncpg.Pool:
        return self._pool

    async def fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        async with self._pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def execute(self, query: str, *args: Any) -> str:
        async with self._pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def close(self) -> None:
        await self._pool.close()


async def get_db() -> Database:
    settings = get_settings()
    pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=1,
        max_size=10,
    )
    assert pool is not None
    return Database(pool)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd api && uv run pytest tests/test_db.py -v`
Expected: 2 passed (requires DATABASE_URL in root .env reachable from `api/`).

- [ ] **Step 5: Commit**

```bash
git add api/app/db.py api/tests/test_db.py
git commit -m "feat(api): asyncpg connection pool"
```

---

### Task B2: Alembic + pgvector + dentalkart_catalog table

**Files:**
- Create: `api/alembic.ini`
- Create: `api/migrations/env.py`
- Create: `api/migrations/script.py.mako`
- Create: `api/migrations/versions/0001_init_catalog.py`

- [ ] **Step 1: Initialise alembic**

Run:
```bash
cd api && uv run alembic init -t async migrations
```
Expected: `alembic.ini` + `migrations/` created.

- [ ] **Step 2: Wire alembic to settings**

Replace `api/migrations/env.py` with:
```python
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.settings import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = None


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
```

- [ ] **Step 3: Write the migration**

Write `api/migrations/versions/0001_init_catalog.py`:
```python
"""init catalog and pgvector

Revision ID: 0001_init_catalog
Revises:
Create Date: 2026-05-13
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_init_catalog"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS dentalkart_catalog (
          id           bigserial PRIMARY KEY,
          sku          text,
          name         text NOT NULL,
          normalized   text NOT NULL,
          brand        text,
          embedding    vector(384) NOT NULL,
          updated_at   timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS dentalkart_catalog_embedding_idx "
        "ON dentalkart_catalog USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS dentalkart_catalog_brand_idx "
        "ON dentalkart_catalog (brand)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dentalkart_catalog")
```

- [ ] **Step 4: Update sqlalchemy.url placeholder in alembic.ini**

In `api/alembic.ini`, ensure the `sqlalchemy.url =` line is blank (env.py sets it). If autogenerated value exists, replace the value with empty string.

- [ ] **Step 5: Run the migration**

Run: `cd api && uv run alembic upgrade head`
Expected: `dentalkart_catalog` table created on the Neon DB.

- [ ] **Step 6: Verify**

Run: `cd api && uv run python -c "import asyncio, asyncpg, os; from app.settings import get_settings; print(asyncio.run(asyncpg.connect(get_settings().database_url).then(lambda c: c.fetchval('SELECT to_regclass(\\'dentalkart_catalog\\')'))))"` — actually simpler: write a one-liner script in the next step.

Instead, run:
```bash
cd api && uv run python -c "
import asyncio, asyncpg
from app.settings import get_settings
async def main():
    c = await asyncpg.connect(get_settings().database_url)
    v = await c.fetchval(\"SELECT to_regclass('dentalkart_catalog')\")
    print(v)
    await c.close()
asyncio.run(main())
"
```
Expected: `dentalkart_catalog`.

- [ ] **Step 7: Commit**

```bash
git add api/alembic.ini api/migrations
git commit -m "feat(api): alembic migration for pgvector + dentalkart_catalog"
```

---

## Section C: Normalization (port from lib/normalize.ts)

### Task C1: normalize module

**Files:**
- Create: `api/app/matching/__init__.py` (empty)
- Create: `api/app/matching/normalize.py`
- Create: `api/tests/matching/__init__.py` (empty)
- Create: `api/tests/matching/test_normalize.py`

- [ ] **Step 1: Write the failing tests**

Write `api/tests/matching/test_normalize.py`:
```python
import pytest

from app.matching.normalize import (
    normalize_for_match,
    strip_noise_suffix,
    strip_pack_suffix,
    strip_sku_tail,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("3M Filtek Z350 XT - SKU: 12345", "3M Filtek Z350 XT"),
        ("GC Fuji IX (SKU: ABC-123)", "GC Fuji IX"),
        ("Dentsply ProTaper - MPN:F2-25", "Dentsply ProTaper"),
        ("Item with no tail", "Item with no tail"),
    ],
)
def test_strip_sku_tail(raw, expected):
    assert strip_sku_tail(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Cotton Rolls Pack Of 500", "Cotton Rolls"),
        ("Burs - Set Of 6", "Burs"),
        ("Capsules (Box Of 50)", "Capsules"),
        ("Cement 25 pcs", "Cement"),
    ],
)
def test_strip_pack_suffix(raw, expected):
    assert strip_pack_suffix(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Composite Resin - Buy Online", "Composite Resin"),
        ("GC Fuji IX | Best Price", "GC Fuji IX"),
        ("Product - Dentalkart.com", "Product"),
        ("Cement - PinkBlue.in", "Cement"),
    ],
)
def test_strip_noise_suffix(raw, expected):
    assert strip_noise_suffix(raw) == expected


def test_normalize_for_match_combined():
    raw = "  3M Filtek Z350 XT   - Pack Of 5  - SKU: ABC-123   "
    assert normalize_for_match(raw) == "3M Filtek Z350 XT"
```

- [ ] **Step 2: Create empty init files**

```bash
mkdir -p api/app/matching api/tests/matching
touch api/app/matching/__init__.py api/tests/matching/__init__.py
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd api && uv run pytest tests/matching/test_normalize.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Implement normalize.py**

Write `api/app/matching/normalize.py`:
```python
"""
Pre-match text normalization. Port of lib/normalize.ts.

Product titles on different sites often append SKUs, pack counts, and
marketplace filler that make two identical products look different to
similarity metrics. Running both strings through normalize_for_match
before comparison eliminates those surface differences without discarding
the product-identity tokens a human reader would use.
"""
from __future__ import annotations

import re

_SKU_TAIL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\s*[-—–|]\s*(sku|mpn|code|item|ref|part)\s*[:#]?\s*[a-z0-9][a-z0-9\-_/]{2,}\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"\s*\((sku|mpn|code|item|ref|part)\s*[:#]?\s*[a-z0-9][a-z0-9\-_/]{2,}\)\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"\s*\[(sku|mpn|code|item|ref|part)\s*[:#]?\s*[a-z0-9][a-z0-9\-_/]{2,}\]\s*$",
        re.IGNORECASE,
    ),
    re.compile(r"\s*[-—–|]\s*[A-Z]{1,4}[-_]?\d{3,}[A-Z0-9]*\s*$"),
]

_PACK_TAIL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\s*[-—–|(]?\s*pack\s*of\s*\d+\s*\)?\s*$", re.IGNORECASE),
    re.compile(r"\s*[-—–|(]?\s*box\s*of\s*\d+\s*\)?\s*$", re.IGNORECASE),
    re.compile(r"\s*[-—–|(]?\s*set\s*of\s*\d+\s*\)?\s*$", re.IGNORECASE),
    re.compile(r"\s*[-—–|(]?\s*\d+\s*(pcs|pc|nos|units?)\s*\)?\s*$", re.IGNORECASE),
    re.compile(r"\s*[-—–|(]?\s*(moq|min\.?\s*order)\s*[:#]?\s*\d+\s*\)?\s*$", re.IGNORECASE),
]

_NOISE_TAIL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\s*[-—–|]\s*(buy\s+online|best\s+price|free\s+shipping|in\s+stock)\s*$",
        re.IGNORECASE,
    ),
    re.compile(r"\s*[-—–|]\s*dentalkart(\.com)?\s*$", re.IGNORECASE),
    re.compile(r"\s*[-—–|]\s*pinkblue(\.in)?\s*$", re.IGNORECASE),
]


def _strip_with(patterns: list[re.Pattern[str]], name: str) -> str:
    out = name
    for pat in patterns:
        out = pat.sub("", out)
    return out.strip()


def strip_sku_tail(name: str) -> str:
    return _strip_with(_SKU_TAIL_PATTERNS, name)


def strip_pack_suffix(name: str) -> str:
    return _strip_with(_PACK_TAIL_PATTERNS, name)


def strip_noise_suffix(name: str) -> str:
    return _strip_with(_NOISE_TAIL_PATTERNS, name)


def normalize_for_match(name: str) -> str:
    cleaned = strip_noise_suffix(strip_pack_suffix(strip_sku_tail(name)))
    return re.sub(r"\s+", " ", cleaned).strip()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd api && uv run pytest tests/matching/test_normalize.py -v`
Expected: all parametrized cases pass.

- [ ] **Step 6: Commit**

```bash
git add api/app/matching/__init__.py api/app/matching/normalize.py \
        api/tests/matching/__init__.py api/tests/matching/test_normalize.py
git commit -m "feat(api): port normalize_for_match from TS"
```

---

## Section D: Attribute extraction & gates (port from smart-matcher.ts)

### Task D1: Attribute extractor (regex features)

**Files:**
- Create: `api/app/matching/attributes.py`
- Create: `api/tests/matching/test_attributes.py`

- [ ] **Step 1: Write the failing tests**

Write `api/tests/matching/test_attributes.py`:
```python
from app.matching.attributes import (
    Attributes,
    extract_attributes,
    extract_brand,
)


def test_extract_brand_takes_first_word_lowercased():
    assert extract_brand("3M Filtek Z350 XT") == "3m"
    assert extract_brand("  GC Fuji IX  ") == "gc"
    assert extract_brand("") == ""


def test_extract_attributes_pack_count():
    a = extract_attributes("Cotton Rolls Pack Of 500")
    assert a.pack_count == 500


def test_extract_attributes_iso_size():
    a = extract_attributes("Endodontic File #25")
    assert a.iso_size == 25


def test_extract_attributes_shade():
    a = extract_attributes("Filtek Z350 XT Shade A2")
    assert a.shade == "a2"


def test_extract_attributes_concentration():
    a = extract_attributes("Chlorhexidine 2% Solution")
    assert a.concentration == 2.0


def test_extract_attributes_model():
    a = extract_attributes("Woodpecker UDS-J Scaler SF-111")
    assert "sf-111" in a.model_codes


def test_extract_attributes_taper():
    a = extract_attributes("ProTaper F2 .06 Taper")
    assert a.taper == "06"


def test_extract_attributes_slot():
    a = extract_attributes("MBT Bracket .022 Slot")
    assert a.slot == "022"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd api && uv run pytest tests/matching/test_attributes.py -v`
Expected: FAIL on import.

- [ ] **Step 3: Implement attributes.py**

Write `api/app/matching/attributes.py`:
```python
"""
Attribute extraction. Port of the regex-based feature extractors that live
inside lib/smart-matcher.ts and lib/variant-extractor.ts. Pure functions —
no I/O. Returned as a dataclass for clean downstream comparison.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(slots=True)
class Attributes:
    brand: str = ""
    model_codes: list[str] = field(default_factory=list)
    iso_size: int | None = None
    shade: str | None = None
    concentration: float | None = None
    taper: str | None = None
    slot: str | None = None
    pack_count: int | None = None
    viscosity: str | None = None


_MODEL_RE = re.compile(r"\b([a-z]{1,5}-?\d{2,5}[a-z]?)\b", re.IGNORECASE)
_ISO_RE = re.compile(r"(?:#|no\.|size|iso)\s*(\d{2,3})\b", re.IGNORECASE)
_SHADE_RE = re.compile(r"\b([A-D][1-4](?:\.5)?|BW|UD)\b")
_CONC_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_TAPER_RE = re.compile(r"\.?(0[2-9])\b")
_SLOT_RE = re.compile(r"\b0?\.?(018|020|022)\b")
_PACK_RE = re.compile(
    r"\b(?:pack\s*of|box\s*of|set\s*of|x\s*)(\d+)\b|\b(\d+)\s*(?:pcs|pc|nos|units?)\b",
    re.IGNORECASE,
)
_VISCOSITY_VARIANTS = ("light body", "heavy body", "putty", "wash", "monophase")


def extract_brand(name: str) -> str:
    parts = name.strip().split()
    if not parts:
        return ""
    return parts[0].lower()


def _first_match(pat: re.Pattern[str], text: str) -> str | None:
    m = pat.search(text)
    return m.group(1) if m else None


def extract_attributes(name: str) -> Attributes:
    lower = name.lower()

    iso_match = _first_match(_ISO_RE, name)
    shade_match = _first_match(_SHADE_RE, name)
    conc_match = _first_match(_CONC_RE, name)
    taper_match = _first_match(_TAPER_RE, name)
    slot_match = _first_match(_SLOT_RE, name)

    pack_count: int | None = None
    pm = _PACK_RE.search(name)
    if pm:
        pack_count = int(pm.group(1) or pm.group(2))

    model_codes = [m.group(1).lower() for m in _MODEL_RE.finditer(name)]

    viscosity: str | None = None
    for v in _VISCOSITY_VARIANTS:
        if v in lower:
            viscosity = v
            break

    return Attributes(
        brand=extract_brand(name),
        model_codes=model_codes,
        iso_size=int(iso_match) if iso_match else None,
        shade=shade_match.lower() if shade_match else None,
        concentration=float(conc_match) if conc_match else None,
        taper=taper_match,
        slot=slot_match,
        pack_count=pack_count,
        viscosity=viscosity,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd api && uv run pytest tests/matching/test_attributes.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add api/app/matching/attributes.py api/tests/matching/test_attributes.py
git commit -m "feat(api): attribute extraction (brand, iso, shade, taper, slot, pack)"
```

---

### Task D2: Gates module (incompatible groups + category exclusions)

**Files:**
- Create: `api/app/matching/gates.py`
- Create: `api/tests/matching/test_gates.py`

- [ ] **Step 1: Write the failing tests**

Write `api/tests/matching/test_gates.py`:
```python
from app.matching.gates import gate_check


def test_brand_mismatch_rejects():
    r = gate_check("3M Filtek Z350", "GC Fuji IX")
    assert r.passed is False
    assert "brand" in r.reason.lower()


def test_incompatible_instrument_rejects():
    r = gate_check("Dentsply Rongeur", "Dentsply Forceps")
    assert r.passed is False
    assert "incompatible" in r.reason.lower()


def test_iso_size_mismatch_rejects():
    r = gate_check("Endo File #15", "Endo File #25")
    assert r.passed is False
    assert "iso" in r.reason.lower()


def test_shade_mismatch_rejects():
    r = gate_check("Filtek Shade A2", "Filtek Shade A3")
    assert r.passed is False


def test_concentration_mismatch_rejects():
    r = gate_check("Chlorhexidine 2%", "Chlorhexidine 5%")
    assert r.passed is False


def test_same_product_passes():
    r = gate_check("3M Filtek Z350 XT Shade A2", "3M Filtek Z350 XT A2")
    assert r.passed is True


def test_category_exclusion_monitor_vs_crown():
    r = gate_check("Monitor LCD 24 inch", "Dental Crown")
    assert r.passed is False


def test_refill_vs_kit_rejects():
    r = gate_check("3M Filtek Refill", "3M Filtek Kit")
    assert r.passed is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd api && uv run pytest tests/matching/test_gates.py -v`
Expected: FAIL on import.

- [ ] **Step 3: Implement gates.py**

Write `api/app/matching/gates.py`:
```python
"""
Hard-conflict gates. Port of lib/smart-matcher.ts conflict logic.

Each gate returns False (and a reason) when the two product names cannot
possibly be the same product. Composed in gate_check(), which is the
single entry point.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.matching.attributes import Attributes, extract_attributes

_INCOMPATIBLE_GROUPS: list[frozenset[str]] = [
    frozenset({
        "rongeur", "forceps", "elevator", "excavator", "explorer",
        "probe", "mirror", "retractor", "plugger", "spreader",
        "condenser", "scissors", "plier", "pliers", "cutter",
        "clamp", "tweezer", "scaler", "curette", "periotome",
        "gauge", "caliper", "file", "files",
        "handpiece", "bur", "burs",
    }),
    frozenset({"liquid", "powder", "gel", "paste", "capsule", "tablet"}),
    frozenset({
        "refill", "refills", "tip", "tips", "replacement", "spare",
        "cartridge", "adapter", "charger", "battery", "kit", "kits",
    }),
    frozenset({
        "motor", "scaler", "scanner", "camera", "autoclave",
        "chair", "stool", "monitor", "light",
    }),
    frozenset({
        "bracket", "brackets", "wire", "wires", "band", "bands",
        "elastic", "elastics", "archwire",
    }),
]

_WORD_TO_GROUP: dict[str, int] = {
    word: idx for idx, group in enumerate(_INCOMPATIBLE_GROUPS) for word in group
}

_CATEGORY_EXCLUSIONS: list[tuple[frozenset[str], frozenset[str]]] = [
    (
        frozenset({"monitor", "tft", "lcd", "screen", "display", "computer"}),
        frozenset({"crown", "crowns", "bracket", "dental"}),
    ),
    (frozenset({"conventional"}), frozenset({"mbt", "roth"})),
    (frozenset({"mbt"}), frozenset({"roth", "conventional", "duploslot"})),
    (frozenset({"roth"}), frozenset({"mbt", "conventional", "duploslot"})),
    (frozenset({"duploslot"}), frozenset({"standard", "mbt", "roth"})),
    (frozenset({"self-ligating"}), frozenset({"conventional"})),
]

_WORD_RE = re.compile(r"\b[a-z0-9]+\b")


@dataclass(slots=True)
class GateResult:
    passed: bool
    reason: str = ""


def _words(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _word_boundary(text: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", text, re.IGNORECASE) is not None


def _brand_match(a: Attributes, search: str, found: str) -> bool:
    if not a.brand:
        return True
    return _word_boundary(found, a.brand)


def _incompatible_types(search_words: set[str], found_words: set[str]) -> bool:
    s_groups = {_WORD_TO_GROUP[w] for w in search_words if w in _WORD_TO_GROUP}
    f_groups = {_WORD_TO_GROUP[w] for w in found_words if w in _WORD_TO_GROUP}
    if not s_groups or not f_groups:
        return False
    s_words = {w for w in search_words if w in _WORD_TO_GROUP}
    f_words = {w for w in found_words if w in _WORD_TO_GROUP}
    for g in s_groups & f_groups:
        s_in_g = {w for w in s_words if _WORD_TO_GROUP[w] == g}
        f_in_g = {w for w in f_words if _WORD_TO_GROUP[w] == g}
        if s_in_g and f_in_g and not (s_in_g & f_in_g):
            return True
    return False


def _category_exclusion(search_words: set[str], found_words: set[str]) -> bool:
    for left, right in _CATEGORY_EXCLUSIONS:
        if (search_words & left and found_words & right) or (
            search_words & right and found_words & left
        ):
            return True
    return False


def gate_check(search: str, found: str) -> GateResult:
    s_attrs = extract_attributes(search)
    f_attrs = extract_attributes(found)

    if not _brand_match(s_attrs, search, found):
        return GateResult(False, f"brand mismatch: '{s_attrs.brand}' not in '{found}'")

    s_words = _words(search)
    f_words = _words(found)

    if _incompatible_types(s_words, f_words):
        return GateResult(False, "incompatible product types")

    if _category_exclusion(s_words, f_words):
        return GateResult(False, "category exclusion")

    if s_attrs.iso_size and f_attrs.iso_size and s_attrs.iso_size != f_attrs.iso_size:
        return GateResult(False, f"iso size mismatch: {s_attrs.iso_size} vs {f_attrs.iso_size}")

    if s_attrs.shade and f_attrs.shade and s_attrs.shade != f_attrs.shade:
        return GateResult(False, f"shade mismatch: {s_attrs.shade} vs {f_attrs.shade}")

    if (
        s_attrs.concentration is not None
        and f_attrs.concentration is not None
        and abs(s_attrs.concentration - f_attrs.concentration) > 1e-6
    ):
        return GateResult(False, "concentration mismatch")

    if s_attrs.taper and f_attrs.taper and s_attrs.taper != f_attrs.taper:
        return GateResult(False, "taper mismatch")

    if s_attrs.slot and f_attrs.slot and s_attrs.slot != f_attrs.slot:
        return GateResult(False, "slot mismatch")

    if (
        s_attrs.model_codes
        and f_attrs.model_codes
        and not (set(s_attrs.model_codes) & set(f_attrs.model_codes))
    ):
        return GateResult(False, "model code mismatch")

    if s_attrs.viscosity and f_attrs.viscosity and s_attrs.viscosity != f_attrs.viscosity:
        return GateResult(False, "viscosity mismatch")

    return GateResult(True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd api && uv run pytest tests/matching/test_gates.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add api/app/matching/gates.py api/tests/matching/test_gates.py
git commit -m "feat(api): hard-conflict gates (brand, incompatible groups, iso, shade, etc.)"
```

---

## Section E: Embedding + index + scoring

### Task E1: Embedding wrapper with model lazy-load

**Files:**
- Create: `api/app/matching/embed.py`
- Create: `api/tests/matching/test_embed.py`

- [ ] **Step 1: Write the failing test**

Write `api/tests/matching/test_embed.py`:
```python
import numpy as np

from app.matching.embed import Embedder


def test_embedder_produces_normalized_384_vec():
    e = Embedder()
    v = e.encode_one("3M Filtek Z350 XT")
    assert v.shape == (384,)
    assert abs(np.linalg.norm(v) - 1.0) < 1e-3


def test_embedder_batch():
    e = Embedder()
    vs = e.encode_many(["GC Fuji IX", "3M Filtek Z350"])
    assert vs.shape == (2, 384)


def test_cosine_self_is_one():
    e = Embedder()
    v = e.encode_one("Dentsply ProTaper F2")
    sim = float(v @ v)
    assert sim > 0.999
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd api && uv run pytest tests/matching/test_embed.py -v`
Expected: FAIL on import.

- [ ] **Step 3: Implement embed.py**

Write `api/app/matching/embed.py`:
```python
"""
Sentence-transformer wrapper. Loads the model once (lazy) and returns
L2-normalized vectors so dot-product equals cosine similarity.
"""
from __future__ import annotations

import threading
from functools import lru_cache

import numpy as np
from sentence_transformers import SentenceTransformer

from app.settings import get_settings


class Embedder:
    _lock = threading.Lock()
    _model: SentenceTransformer | None = None

    def __init__(self) -> None:
        with Embedder._lock:
            if Embedder._model is None:
                s = get_settings()
                Embedder._model = SentenceTransformer(s.embed_model, device=s.embed_device)
        assert Embedder._model is not None
        self._model: SentenceTransformer = Embedder._model

    def encode_one(self, text: str) -> np.ndarray:
        v = self._model.encode([text], normalize_embeddings=True)[0]
        return np.asarray(v, dtype=np.float32)

    def encode_many(self, texts: list[str]) -> np.ndarray:
        vs = self._model.encode(list(texts), normalize_embeddings=True, batch_size=32)
        return np.asarray(vs, dtype=np.float32)


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    return Embedder()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd api && uv run pytest tests/matching/test_embed.py -v`
Expected: 3 passed. First run downloads ~90 MB model; subsequent runs are fast.

- [ ] **Step 5: Commit**

```bash
git add api/app/matching/embed.py api/tests/matching/test_embed.py
git commit -m "feat(api): sentence-transformer embedder with normalized vectors"
```

---

### Task E2: Score module (weighted combination → verdict)

**Files:**
- Create: `api/app/matching/score.py`
- Create: `api/tests/matching/test_score.py`

- [ ] **Step 1: Write the failing tests**

Write `api/tests/matching/test_score.py`:
```python
from app.matching.attributes import Attributes
from app.matching.score import Verdict, score_match


def test_high_cosine_same_brand_passes_accept():
    r = score_match(
        cosine_sim=0.95,
        search_attrs=Attributes(brand="3m"),
        candidate_attrs=Attributes(brand="3m"),
    )
    assert r.verdict == Verdict.CONFIRMED
    assert r.score >= 0.75


def test_mid_cosine_landing_in_possible():
    r = score_match(
        cosine_sim=0.55,
        search_attrs=Attributes(brand="gc"),
        candidate_attrs=Attributes(brand="gc"),
    )
    assert r.verdict in (Verdict.POSSIBLE, Verdict.VARIANT)


def test_low_cosine_brand_mismatch_rejects():
    r = score_match(
        cosine_sim=0.2,
        search_attrs=Attributes(brand="3m"),
        candidate_attrs=Attributes(brand="gc"),
    )
    assert r.verdict == Verdict.REJECTED


def test_pack_match_perfect_within_2pct():
    r = score_match(
        cosine_sim=0.9,
        search_attrs=Attributes(brand="x", pack_count=500),
        candidate_attrs=Attributes(brand="x", pack_count=505),
    )
    assert r.verdict == Verdict.CONFIRMED


def test_pack_mismatch_lowers_score():
    r_match = score_match(
        cosine_sim=0.9,
        search_attrs=Attributes(brand="x", pack_count=10),
        candidate_attrs=Attributes(brand="x", pack_count=10),
    )
    r_mismatch = score_match(
        cosine_sim=0.9,
        search_attrs=Attributes(brand="x", pack_count=10),
        candidate_attrs=Attributes(brand="x", pack_count=100),
    )
    assert r_match.score > r_mismatch.score
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd api && uv run pytest tests/matching/test_score.py -v`
Expected: FAIL on import.

- [ ] **Step 3: Implement score.py**

Write `api/app/matching/score.py`:
```python
"""
Weighted scoring + verdict mapping. Pure function — no I/O.

score = w_cosine * cosine + w_brand * brand + w_pack * pack + w_attr * attr
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.matching.attributes import Attributes
from app.settings import get_settings

_PACK_TOLERANCE = 0.02  # 2%


class Verdict(str, Enum):
    CONFIRMED = "confirmed"
    POSSIBLE = "possible"
    VARIANT = "variant"
    REJECTED = "rejected"


@dataclass(slots=True)
class MatchScore:
    score: float
    cosine: float
    brand_score: float
    pack_score: float
    attr_score: float
    verdict: Verdict


def _brand_score(s: Attributes, c: Attributes) -> float:
    if not s.brand:
        return 1.0
    return 1.0 if s.brand == c.brand else 0.0


def _pack_score(s: Attributes, c: Attributes) -> float:
    if s.pack_count is None or c.pack_count is None:
        return 1.0
    bigger = max(s.pack_count, c.pack_count)
    if bigger == 0:
        return 1.0
    diff = abs(s.pack_count - c.pack_count) / bigger
    return 1.0 if diff <= _PACK_TOLERANCE else max(0.0, 1.0 - diff)


def _attr_score(s: Attributes, c: Attributes) -> float:
    checks: list[float] = []
    for attr in ("iso_size", "shade", "concentration", "taper", "slot", "viscosity"):
        sv = getattr(s, attr)
        cv = getattr(c, attr)
        if sv is None or cv is None:
            continue
        checks.append(1.0 if sv == cv else 0.0)
    if s.model_codes and c.model_codes:
        checks.append(1.0 if set(s.model_codes) & set(c.model_codes) else 0.0)
    return sum(checks) / len(checks) if checks else 1.0


def score_match(
    cosine_sim: float,
    search_attrs: Attributes,
    candidate_attrs: Attributes,
) -> MatchScore:
    settings = get_settings()
    brand = _brand_score(search_attrs, candidate_attrs)
    pack = _pack_score(search_attrs, candidate_attrs)
    attr = _attr_score(search_attrs, candidate_attrs)

    score = (
        settings.score_w_cosine * cosine_sim
        + settings.score_w_brand * brand
        + settings.score_w_pack * pack
        + settings.score_w_attr * attr
    )

    if score >= settings.accept_threshold:
        verdict = Verdict.CONFIRMED
    elif score >= settings.possible_threshold:
        verdict = Verdict.POSSIBLE
    elif score >= settings.variant_threshold:
        verdict = Verdict.VARIANT
    else:
        verdict = Verdict.REJECTED

    return MatchScore(
        score=score,
        cosine=cosine_sim,
        brand_score=brand,
        pack_score=pack,
        attr_score=attr,
        verdict=verdict,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd api && uv run pytest tests/matching/test_score.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add api/app/matching/score.py api/tests/matching/test_score.py
git commit -m "feat(api): weighted score (cosine+brand+pack+attr) and verdict mapping"
```

---

### Task E3: Triage orchestrator (gates → cosine → score → verdict)

**Files:**
- Create: `api/app/matching/triage.py`
- Create: `api/tests/matching/test_triage.py`

- [ ] **Step 1: Write the failing tests**

Write `api/tests/matching/test_triage.py`:
```python
from app.matching.score import Verdict
from app.matching.triage import triage


def test_brand_mismatch_short_circuits_to_rejected():
    r = triage("3M Filtek Z350 XT", "GC Fuji IX")
    assert r.verdict == Verdict.REJECTED
    assert "brand" in r.reasons[0].lower()


def test_same_product_confirmed():
    r = triage("3M Filtek Z350 XT Shade A2", "3M Filtek Z350 XT A2")
    assert r.verdict == Verdict.CONFIRMED


def test_iso_conflict_rejected():
    r = triage("Dentsply Endo File #15", "Dentsply Endo File #25")
    assert r.verdict == Verdict.REJECTED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd api && uv run pytest tests/matching/test_triage.py -v`
Expected: FAIL on import.

- [ ] **Step 3: Implement triage.py**

Write `api/app/matching/triage.py`:
```python
"""
Top-level matching orchestrator.

Flow per (search, candidate) pair:
  1. normalize both
  2. hard gates → reject if any conflict
  3. embed both → cosine similarity
  4. extract attributes
  5. weighted score → verdict
"""
from __future__ import annotations

from dataclasses import dataclass

from app.matching.attributes import extract_attributes
from app.matching.embed import get_embedder
from app.matching.gates import gate_check
from app.matching.normalize import normalize_for_match
from app.matching.score import MatchScore, Verdict, score_match


@dataclass(slots=True)
class TriageResult:
    verdict: Verdict
    score: float
    cosine: float
    reasons: list[str]


def triage(search: str, candidate: str) -> TriageResult:
    s_norm = normalize_for_match(search)
    c_norm = normalize_for_match(candidate)

    if not s_norm or not c_norm:
        return TriageResult(Verdict.REJECTED, 0.0, 0.0, ["empty string"])

    gate = gate_check(s_norm, c_norm)
    if not gate.passed:
        return TriageResult(Verdict.REJECTED, 0.0, 0.0, [gate.reason])

    embedder = get_embedder()
    vecs = embedder.encode_many([s_norm, c_norm])
    cosine = float(vecs[0] @ vecs[1])

    s_attrs = extract_attributes(s_norm)
    c_attrs = extract_attributes(c_norm)

    ms: MatchScore = score_match(cosine, s_attrs, c_attrs)

    return TriageResult(
        verdict=ms.verdict,
        score=ms.score,
        cosine=cosine,
        reasons=[
            f"cosine={cosine:.3f}",
            f"brand={ms.brand_score:.0f}",
            f"pack={ms.pack_score:.2f}",
            f"attr={ms.attr_score:.2f}",
        ],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd api && uv run pytest tests/matching/test_triage.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add api/app/matching/triage.py api/tests/matching/test_triage.py
git commit -m "feat(api): triage orchestrator (normalize → gates → embed → score)"
```

---

### Task E4: Catalog index (pgvector queries)

**Files:**
- Create: `api/app/matching/index.py`
- Create: `api/tests/matching/test_index.py`

- [ ] **Step 1: Write the failing tests**

Write `api/tests/matching/test_index.py`:
```python
import pytest

from app.db import get_db
from app.matching.embed import get_embedder
from app.matching.index import CatalogIndex


@pytest.mark.asyncio
async def test_index_search_returns_topk():
    db = await get_db()
    try:
        emb = get_embedder()
        await db.execute("DELETE FROM dentalkart_catalog")
        names = [
            "3M Filtek Z350 XT Shade A2",
            "3M Filtek Z350 XT Shade A3",
            "GC Fuji IX GP Capsules",
            "Dentsply ProTaper Universal F2",
        ]
        vs = emb.encode_many(names)
        for n, v in zip(names, vs, strict=True):
            await db.execute(
                "INSERT INTO dentalkart_catalog (name, normalized, brand, embedding) "
                "VALUES ($1, $2, $3, $4::vector)",
                n,
                n.lower(),
                n.split()[0].lower(),
                "[" + ",".join(f"{x:.6f}" for x in v.tolist()) + "]",
            )

        idx = CatalogIndex(db)
        hits = await idx.top_k("Filtek Z350 A2", k=2)
        assert len(hits) == 2
        assert "Filtek" in hits[0].name
    finally:
        await db.execute("DELETE FROM dentalkart_catalog")
        await db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd api && uv run pytest tests/matching/test_index.py -v`
Expected: FAIL on import.

- [ ] **Step 3: Implement index.py**

Write `api/app/matching/index.py`:
```python
"""
Catalog vector index. Uses pgvector's cosine operator (<=>) for top-K
recall over the dentalkart_catalog table. FAISS-free for now; we'll
introduce FAISS only if pgvector becomes a bottleneck at scale.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.db import Database
from app.matching.embed import get_embedder


@dataclass(slots=True)
class CatalogHit:
    id: int
    name: str
    sku: str | None
    brand: str | None
    distance: float

    @property
    def cosine(self) -> float:
        return 1.0 - self.distance


def _vec_literal(v) -> str:
    return "[" + ",".join(f"{float(x):.6f}" for x in v.tolist()) + "]"


class CatalogIndex:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def top_k(self, query: str, k: int = 50) -> list[CatalogHit]:
        emb = get_embedder()
        vec = emb.encode_one(query)
        rows = await self._db.fetch(
            """
            SELECT id, name, sku, brand,
                   embedding <=> $1::vector AS distance
            FROM dentalkart_catalog
            ORDER BY embedding <=> $1::vector
            LIMIT $2
            """,
            _vec_literal(vec),
            k,
        )
        return [
            CatalogHit(
                id=r["id"],
                name=r["name"],
                sku=r["sku"],
                brand=r["brand"],
                distance=float(r["distance"]),
            )
            for r in rows
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd api && uv run pytest tests/matching/test_index.py -v`
Expected: 1 passed (writes + cleans up a small batch on the Neon DB).

- [ ] **Step 5: Commit**

```bash
git add api/app/matching/index.py api/tests/matching/test_index.py
git commit -m "feat(api): pgvector-backed catalog top-K index"
```

---

## Section F: /match endpoint + parity tests

### Task F1: Pydantic request/response models

**Files:**
- Create: `api/app/schemas.py`
- Create: `api/tests/test_schemas.py`

- [ ] **Step 1: Write the failing tests**

Write `api/tests/test_schemas.py`:
```python
import pytest
from pydantic import ValidationError

from app.schemas import MatchRequest, MatchResponse, RankedCandidate


def test_match_request_requires_search_and_candidates():
    req = MatchRequest(search="3M Filtek", candidates=["3M Filtek Z350"])
    assert req.search == "3M Filtek"
    assert req.candidates == ["3M Filtek Z350"]


def test_match_request_rejects_empty_search():
    with pytest.raises(ValidationError):
        MatchRequest(search="", candidates=["x"])


def test_match_request_rejects_empty_candidates():
    with pytest.raises(ValidationError):
        MatchRequest(search="x", candidates=[])


def test_ranked_candidate_shape():
    rc = RankedCandidate(
        candidate="3M Filtek Z350",
        verdict="confirmed",
        score=0.92,
        cosine=0.95,
        reasons=["cosine=0.95"],
    )
    assert rc.verdict == "confirmed"


def test_match_response_keeps_candidates_sorted():
    resp = MatchResponse(
        ranked=[
            RankedCandidate(candidate="b", verdict="confirmed", score=0.9, cosine=0.9, reasons=[]),
            RankedCandidate(candidate="a", verdict="possible", score=0.6, cosine=0.6, reasons=[]),
        ]
    )
    assert resp.ranked[0].score >= resp.ranked[1].score
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd api && uv run pytest tests/test_schemas.py -v`
Expected: FAIL on import.

- [ ] **Step 3: Implement schemas.py**

Write `api/app/schemas.py`:
```python
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


Verdict = Literal["confirmed", "possible", "variant", "rejected"]


class MatchRequest(BaseModel):
    search: str = Field(min_length=1)
    candidates: list[str] = Field(min_length=1)

    @field_validator("candidates")
    @classmethod
    def _no_empty(cls, v: list[str]) -> list[str]:
        cleaned = [c for c in v if c and c.strip()]
        if not cleaned:
            raise ValueError("at least one non-empty candidate required")
        return cleaned


class RankedCandidate(BaseModel):
    candidate: str
    verdict: Verdict
    score: float
    cosine: float
    reasons: list[str]


class MatchResponse(BaseModel):
    ranked: list[RankedCandidate]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd api && uv run pytest tests/test_schemas.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add api/app/schemas.py api/tests/test_schemas.py
git commit -m "feat(api): pydantic schemas for /match"
```

---

### Task F2: /match route

**Files:**
- Create: `api/app/routes/__init__.py` (empty)
- Create: `api/app/routes/match.py`
- Modify: `api/app/main.py`
- Create: `api/tests/routes/__init__.py` (empty)
- Create: `api/tests/routes/test_match_route.py`

- [ ] **Step 1: Write the failing test**

Write `api/tests/routes/test_match_route.py`:
```python
from fastapi.testclient import TestClient

from app.main import app


def test_match_endpoint_returns_ranked_results():
    client = TestClient(app)
    res = client.post(
        "/match",
        json={
            "search": "3M Filtek Z350 XT Shade A2",
            "candidates": [
                "3M Filtek Z350 XT A2",
                "GC Fuji IX GP Capsules",
                "3M Filtek Z350 XT Shade A3",
            ],
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert "ranked" in data
    assert len(data["ranked"]) == 3
    # First candidate (A2 vs A2) wins
    assert data["ranked"][0]["candidate"].endswith("A2")
    assert data["ranked"][0]["verdict"] in ("confirmed", "possible")
    # Brand mismatch is last and rejected
    last = data["ranked"][-1]
    assert last["candidate"].startswith("GC")
    assert last["verdict"] == "rejected"


def test_match_endpoint_rejects_empty_search():
    client = TestClient(app)
    res = client.post("/match", json={"search": "", "candidates": ["x"]})
    assert res.status_code == 422
```

- [ ] **Step 2: Create empty init files**

```bash
mkdir -p api/app/routes api/tests/routes
touch api/app/routes/__init__.py api/tests/routes/__init__.py
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd api && uv run pytest tests/routes/test_match_route.py -v`
Expected: FAIL — route not mounted yet.

- [ ] **Step 4: Implement the route**

Write `api/app/routes/match.py`:
```python
from __future__ import annotations

import heapq

from fastapi import APIRouter

from app.matching.triage import triage
from app.schemas import MatchRequest, MatchResponse, RankedCandidate

router = APIRouter()


@router.post("/match", response_model=MatchResponse)
def post_match(req: MatchRequest) -> MatchResponse:
    scored: list[tuple[float, RankedCandidate]] = []
    for cand in req.candidates:
        r = triage(req.search, cand)
        scored.append(
            (
                r.score,
                RankedCandidate(
                    candidate=cand,
                    verdict=r.verdict.value,
                    score=r.score,
                    cosine=r.cosine,
                    reasons=r.reasons,
                ),
            )
        )
    ranked = [rc for _, rc in heapq.nlargest(len(scored), scored, key=lambda x: x[0])]
    return MatchResponse(ranked=ranked)
```

- [ ] **Step 5: Mount the router**

Replace `api/app/main.py`:
```python
from fastapi import FastAPI

from app.routes import match as match_route

app = FastAPI(title="product-compare-api", version="0.1.0")
app.include_router(match_route.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd api && uv run pytest tests/routes/test_match_route.py -v`
Expected: 2 passed.

- [ ] **Step 7: Commit**

```bash
git add api/app/routes/__init__.py api/app/routes/match.py api/app/main.py \
        api/tests/routes/__init__.py api/tests/routes/test_match_route.py
git commit -m "feat(api): POST /match endpoint with ranked output"
```

---

### Task F3: Parity test against current TS triage on a fixed corpus

**Files:**
- Create: `api/tests/parity/__init__.py` (empty)
- Create: `api/tests/parity/fixtures/ts_verdicts.json`
- Create: `api/tests/parity/test_parity_ts.py`
- Create: `api/scripts/dump_ts_verdicts.ts`

- [ ] **Step 1: Build the TS verdict fixture**

Run: `mkdir -p api/scripts`

Write `api/scripts/dump_ts_verdicts.ts`:
```typescript
import { triage } from "../../lib/match-triage";

const CASES: Array<[string, string]> = [
  ["3M Filtek Z350 XT Shade A2", "3M Filtek Z350 XT A2"],
  ["3M Filtek Z350 XT Shade A2", "3M Filtek Z350 XT Shade A3"],
  ["3M Filtek Z350 XT", "GC Fuji IX GP Capsules"],
  ["Dentsply ProTaper F2", "Dentsply ProTaper F1"],
  ["Dentsply ProTaper F2 25", "Dentsply ProTaper F2"],
  ["GC Fuji IX GP Capsules", "GC Fuji IX GP Powder"],
  ["GC Fuji IX GP Capsules", "GC Fuji IX GP Capsules - Pack Of 50"],
  ["Endo File #25", "Endo File #15"],
  ["Cotton Rolls Pack Of 500", "Cotton Rolls"],
  ["Chlorhexidine 2% Mouthwash", "Chlorhexidine 5% Mouthwash"],
  ["MBT Bracket .022 Slot", "Roth Bracket .022 Slot"],
  ["MBT Bracket .022 Slot", "MBT Bracket .018 Slot"],
  ["3M Espe Adper Single Bond 2", "3M Espe Adper Single Bond Universal"],
  ["Septodont Septanest 1:100000", "Septodont Septanest 1:200000"],
  ["Woodpecker UDS-J Scaler", "Woodpecker UDS-N3 Scaler"],
  ["Composite Resin - Buy Online", "Composite Resin"],
  ["3M Filtek Refill", "3M Filtek Kit"],
  ["Monitor LCD 24 inch", "Dental Crown"],
  ["GC Fuji IX GP Extra", "GC FujiIX GP Capsules"],
  ["Putty Light Body", "Putty Heavy Body"],
];

const results = CASES.map(([s, c]) => {
  const t = triage(s, c);
  return { search: s, candidate: c, verdict: t.verdict, similarity: t.similarity };
});

console.log(JSON.stringify(results, null, 2));
```

- [ ] **Step 2: Run it and save fixture**

```bash
mkdir -p api/tests/parity/fixtures
cd /Users/maclapctp85/Desktop/updated-product-compare && \
  npx tsx api/scripts/dump_ts_verdicts.ts > api/tests/parity/fixtures/ts_verdicts.json
```
Expected: JSON array of 20 entries.

- [ ] **Step 3: Create empty init**

```bash
mkdir -p api/tests/parity
touch api/tests/parity/__init__.py
```

- [ ] **Step 4: Write parity test**

Write `api/tests/parity/test_parity_ts.py`:
```python
"""
Parity test: compare Python /match verdict vs TS triage verdict on a
fixed corpus. The Python pipeline is allowed to disagree on edge cases
(it uses embeddings, TS used string-similarity), but it MUST agree on
clear cases.

TS verdict mapping → Python verdict:
  accept → confirmed
  reject → rejected
  grey   → possible | variant   (both are acceptable)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

FIXTURE = Path(__file__).parent / "fixtures" / "ts_verdicts.json"


def _expected_python(verdict: str) -> set[str]:
    return {
        "accept": {"confirmed"},
        "reject": {"rejected"},
        "grey": {"possible", "variant"},
    }[verdict]


@pytest.mark.parametrize("case", json.loads(FIXTURE.read_text()))
def test_python_matches_ts(case: dict):
    client = TestClient(app)
    res = client.post(
        "/match",
        json={"search": case["search"], "candidates": [case["candidate"]]},
    )
    assert res.status_code == 200
    py_verdict = res.json()["ranked"][0]["verdict"]
    expected = _expected_python(case["verdict"])
    assert py_verdict in expected, (
        f"TS said {case['verdict']} (→ {expected}), Python said {py_verdict} "
        f"for ({case['search']!r}, {case['candidate']!r})"
    )
```

- [ ] **Step 5: Run parity tests**

Run: `cd api && uv run pytest tests/parity/test_parity_ts.py -v`
Expected: ≥18/20 passed. If 2+ fail, inspect; mismatches surface places where the Python pipeline behaves differently. Tune thresholds in `.env` or `settings.py` defaults until ≥90% pass.

- [ ] **Step 6: Commit**

```bash
git add api/scripts/dump_ts_verdicts.ts \
        api/tests/parity/__init__.py \
        api/tests/parity/fixtures/ts_verdicts.json \
        api/tests/parity/test_parity_ts.py
git commit -m "test(api): TS↔Python parity test on a 20-case fixture"
```

---

## Section G: Catalog ingestion script

### Task G1: build_catalog_index.py — ingest Dentalkart corpus

**Files:**
- Create: `api/scripts/__init__.py` (empty)
- Create: `api/scripts/build_catalog_index.py`
- Create: `api/tests/test_build_catalog.py`

- [ ] **Step 1: Make `api/scripts` a package**

Run:
```bash
mkdir -p api/scripts
touch api/scripts/__init__.py
```

The existing `api/pyproject.toml` already has `pythonpath = ["."]`, so `from scripts.build_catalog_index import ingest_csv` resolves correctly when pytest runs with `cwd=api/`.

- [ ] **Step 2: Write the failing test**

Write `api/tests/test_build_catalog.py`:
```python
import csv
from pathlib import Path

import pytest

from app.db import get_db
from scripts.build_catalog_index import ingest_csv


@pytest.mark.asyncio
async def test_ingest_csv_writes_rows(tmp_path: Path):
    p = tmp_path / "products.csv"
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "sku"])
        w.writerow(["3M Filtek Z350 XT", "FILTEK-001"])
        w.writerow(["GC Fuji IX GP Capsules", "FUJI-IX-001"])

    db = await get_db()
    try:
        await db.execute("DELETE FROM dentalkart_catalog")
        n = await ingest_csv(p, db)
        assert n == 2
        rows = await db.fetch("SELECT name FROM dentalkart_catalog ORDER BY name")
        assert [r["name"] for r in rows] == [
            "3M Filtek Z350 XT",
            "GC Fuji IX GP Capsules",
        ]
    finally:
        await db.execute("DELETE FROM dentalkart_catalog")
        await db.close()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd api && uv run pytest tests/test_build_catalog.py -v`
Expected: FAIL on `ModuleNotFoundError: scripts.build_catalog_index`.

- [ ] **Step 4: Implement the script**

Write `api/scripts/build_catalog_index.py`:
```python
"""
Ingest a CSV of Dentalkart product names into the dentalkart_catalog
table, embedding each name with the sentence-transformer model.

Usage:
    uv run python scripts/build_catalog_index.py path/to/products.csv

CSV columns expected (case-insensitive):
    name  (required) — product display name
    sku   (optional) — Dentalkart SKU
    brand (optional) — overrides first-word brand inference
"""
from __future__ import annotations

import argparse
import asyncio
import csv
from pathlib import Path

from app.db import Database, get_db
from app.matching.embed import get_embedder
from app.matching.normalize import normalize_for_match


def _vec_literal(v) -> str:
    return "[" + ",".join(f"{float(x):.6f}" for x in v.tolist()) + "]"


async def ingest_csv(path: Path, db: Database, batch: int = 64) -> int:
    emb = get_embedder()
    rows: list[dict[str, str]] = []
    with path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            keyed = {k.lower().strip(): (v or "").strip() for k, v in row.items()}
            if not keyed.get("name"):
                continue
            rows.append(keyed)

    inserted = 0
    for i in range(0, len(rows), batch):
        chunk = rows[i : i + batch]
        names = [r["name"] for r in chunk]
        vecs = emb.encode_many(names)
        for r, v in zip(chunk, vecs, strict=True):
            brand = r.get("brand") or r["name"].split()[0].lower() if r["name"] else None
            await db.execute(
                "INSERT INTO dentalkart_catalog (name, normalized, brand, sku, embedding) "
                "VALUES ($1, $2, $3, $4, $5::vector)",
                r["name"],
                normalize_for_match(r["name"]).lower(),
                brand,
                r.get("sku") or None,
                _vec_literal(v),
            )
            inserted += 1
    return inserted


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", type=Path)
    parser.add_argument("--truncate", action="store_true")
    args = parser.parse_args()

    db = await get_db()
    try:
        if args.truncate:
            await db.execute("DELETE FROM dentalkart_catalog")
        n = await ingest_csv(args.csv, db)
        print(f"inserted {n} rows")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(_main())
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd api && uv run pytest tests/test_build_catalog.py -v`
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add api/scripts/__init__.py api/scripts/build_catalog_index.py \
        api/tests/test_build_catalog.py
git commit -m "feat(api): catalog ingestion script (CSV → embed → pgvector)"
```

---

## Section H: Full-suite verification

### Task H1: Run full test suite + ruff + mypy

**Files:**
- (none — verification only)

- [ ] **Step 1: Run the full suite**

Run: `cd api && uv run pytest -v --tb=short`
Expected: All sections green. If parity tests are below 90%, investigate top mismatches and tune thresholds in `app/settings.py` defaults (NOT individual rules — we want the system tunable, not over-fitted).

- [ ] **Step 2: Lint**

Run: `cd api && uv run ruff check .`
Expected: No errors.

- [ ] **Step 3: Type check**

Run: `cd api && uv run mypy app`
Expected: `Success: no issues found` (or short list of clearly-fixable issues — fix and re-run before committing).

- [ ] **Step 4: Boot the server and curl /match**

Run (in one terminal): `cd api && uv run uvicorn app.main:app --port 8000`

In another:
```bash
curl -s -X POST http://localhost:8000/match \
  -H 'content-type: application/json' \
  -d '{"search":"3M Filtek Z350 XT Shade A2","candidates":["3M Filtek Z350 XT A2","GC Fuji IX","3M Filtek Z350 XT Shade A3"]}' | python -m json.tool
```
Expected: JSON `ranked` array with the A2 candidate first (verdict `confirmed` or `possible`), GC Fuji rejected.

- [ ] **Step 5: Commit verification notes (none, just stop here)**

No commit. Plan 1 is complete.

---

## Definition of Done

- `cd api && uv run pytest` passes.
- `cd api && uv run ruff check .` and `uv run mypy app` both clean.
- `dentalkart_catalog` table exists on the Neon DB with the pgvector index.
- `POST http://localhost:8000/match` returns ranked verdicts on the sanity request.
- TS↔Python parity: ≥18/20 cases agree on the fixed fixture.
- Branch is clean; every section above is a commit.

## What's next (Plan 2 preview)

- Port `lib/scrapers/dentalkart.ts` to `api/app/scrapers/dentalkart.py` (httpx + selectolax).
- Add `POST /compare` that searches Dentalkart and returns a single-source `ComparisonResult` using `/match` internally for candidate ranking.
- Flip `app/api/scrape/route.ts` to forward to Python for the Dentalkart path only, behind a feature flag.
