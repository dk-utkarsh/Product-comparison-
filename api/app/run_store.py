"""
SQLite store for scheduled SKU-comparison runs.

Stand-alone (no app imports beyond settings) so it never causes import cycles.
Two tables: `runs` (one per scheduled/triggered execution) and `run_items` (one
per SKU, holding the full CompareResult JSON). Survives restarts; old runs are
pruned by retention. All access is synchronous sqlite3 — runs are small and
infrequent, so a connection-per-call keeps it simple and thread-safe.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.settings import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,          -- running | done | error
    trigger     TEXT NOT NULL,          -- scheduled | manual | rerun
    sku_count   INTEGER DEFAULT 0,
    done_count  INTEGER DEFAULT 0,
    error       TEXT,
    source_run_id INTEGER               -- when trigger=rerun, the run it copied
);
CREATE TABLE IF NOT EXISTS watchlist (
    sku      TEXT PRIMARY KEY,
    name     TEXT NOT NULL,
    added_at TEXT
);
CREATE TABLE IF NOT EXISTS run_items (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id   INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    idx      INTEGER NOT NULL,
    sku      TEXT,
    name     TEXT,
    result   TEXT,                      -- CompareResult JSON
    UNIQUE(run_id, idx)
);
CREATE INDEX IF NOT EXISTS ix_run_items_run ON run_items(run_id);
CREATE TABLE IF NOT EXISTS reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    product     TEXT NOT NULL,          -- input product name reviewed
    dk_matched  TEXT,                   -- what DK resolved to (for context)
    correct     INTEGER NOT NULL,       -- 1 = reviewer says correct, 0 = needs fix
    message     TEXT,                   -- improvement note when not correct
    result      TEXT,                   -- full CompareResult JSON snapshot
    run_id      INTEGER                 -- the scheduled run reviewed (else NULL)
);
CREATE INDEX IF NOT EXISTS ix_reviews_correct ON reviews(correct);
"""
# NOTE: `confirmed_matches` (the 👍/👎 learning loop) is NOT in the local SQLite —
# it lives on the SHARED Neon DB (see the Postgres helpers below) so a keep/hide
# made on ANY environment applies everywhere. Everything else here (runs, run_items,
# reviews, watchlist) is a per-machine local file so the /runs page stays instant.


def _db_path() -> Path:
    p = Path(get_settings().runs_db_path)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent / p  # api/<runs_db_path>
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_db_path(), timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def _migrate(c: sqlite3.Connection) -> None:
    """Add columns to pre-existing tables (CREATE IF NOT EXISTS won't alter them)."""
    for table, col, decl in (
        ("reviews", "run_id", "INTEGER"),
        ("runs", "source_run_id", "INTEGER"),
    ):
        cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def init_db() -> None:
    with _conn() as c:
        c.executescript(_SCHEMA)
        _migrate(c)


def create_run(started_at: str, trigger: str, sku_count: int,
               source_run_id: int | None = None) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO runs (started_at, status, trigger, sku_count, source_run_id) "
            "VALUES (?,?,?,?,?)",
            (started_at, "running", trigger, sku_count, source_run_id),
        )
        return int(cur.lastrowid)


# ── watchlist (the fixed products kept in every run for price tracking) ──
def get_watchlist() -> list[dict[str, Any]]:
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT sku, name FROM watchlist ORDER BY added_at, sku").fetchall()]


def set_watchlist(items: list[dict[str, str]], added_at: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM watchlist")
        c.executemany(
            "INSERT OR REPLACE INTO watchlist (sku, name, added_at) VALUES (?,?,?)",
            [(i["sku"], i["name"], added_at) for i in items],
        )


def add_to_watchlist(items: list[dict[str, str]], added_at: str) -> None:
    with _conn() as c:
        c.executemany(
            "INSERT OR IGNORE INTO watchlist (sku, name, added_at) VALUES (?,?,?)",
            [(i["sku"], i["name"], added_at) for i in items],
        )


def save_item(run_id: int, idx: int, sku: str, name: str, result: dict[str, Any]) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO run_items (run_id, idx, sku, name, result) VALUES (?,?,?,?,?)",
            (run_id, idx, sku, name, json.dumps(result)),
        )
        c.execute("UPDATE runs SET done_count = done_count + 1 WHERE id=?", (run_id,))


def finish_run(run_id: int, finished_at: str, status: str, error: str | None = None) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE runs SET finished_at=?, status=?, error=? WHERE id=?",
            (finished_at, status, error, run_id),
        )


def list_runs(limit: int = 200) -> list[dict[str, Any]]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["review"] = run_review_summary(d["id"])
            out.append(d)
    return out


def get_run(run_id: int) -> dict[str, Any] | None:
    with _conn() as c:
        run = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if run is None:
            return None
        items = c.execute(
            "SELECT idx, sku, name, result FROM run_items WHERE run_id=? ORDER BY idx",
            (run_id,),
        ).fetchall()
    out = dict(run)
    out["review"] = run_review_summary(run_id)
    out["items"] = [
        {"idx": it["idx"], "sku": it["sku"], "name": it["name"],
         "result": json.loads(it["result"]) if it["result"] else None}
        for it in items
    ]
    return out


def all_run_items() -> list[dict[str, Any]]:
    """Every run_item with a result, oldest→newest (by run start), for the pricing
    insights. Powers the OVERALL view (de-duped to the latest per product upstream)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT i.run_id, r.started_at, i.sku, i.name, i.result "
            "FROM run_items i JOIN runs r ON r.id = i.run_id "
            "WHERE i.result IS NOT NULL ORDER BY r.started_at"
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            res = json.loads(row["result"])
        except (TypeError, ValueError):
            continue
        out.append({"name": row["name"], "sku": row["sku"],
                    "started_at": row["started_at"], "result": res})
    return out


def hidden_map() -> dict[str, set[str]]:
    """{dk_key → {competitor_id, …}} of everything the user has HIDDEN (label=
    no_match), so the insights can exclude those competitors. From the SHARED Neon
    store; degrades to empty on a DB blip (nothing excluded rather than an error)."""
    try:
        _ensure_confirmed()
        with _pgc() as c:
            rows = c.execute(
                "SELECT dk_key, competitor_id FROM confirmed_matches WHERE label='no_match'"
            ).fetchall()
        m: dict[str, set[str]] = {}
        for r in rows:
            m.setdefault(r["dk_key"], set()).add(r["competitor_id"])
        return m
    except Exception:
        return {}


def price_history(name: str) -> list[dict[str, Any]]:
    """Every stored price point for a product (by name), oldest first — DK +
    each competitor (shown matches only) per run that included it. Powers the
    per-product price-change view."""
    with _conn() as c:
        rows = c.execute(
            "SELECT r.id run_id, r.started_at, i.result "
            "FROM run_items i JOIN runs r ON r.id = i.run_id "
            "WHERE i.name = ? AND i.result IS NOT NULL ORDER BY r.started_at",
            (name,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            res = json.loads(row["result"])
        except (TypeError, ValueError):
            continue
        dk = res.get("dentalkart_match") or {}
        comps = {
            c["competitor_id"]: c.get("matched_price")
            for c in res.get("competitors", [])
            if c.get("matched_name") and (c.get("score") or 0) >= 0.7 and c.get("matched_price")
        }
        out.append({
            "run_id": row["run_id"], "time": row["started_at"],
            "dk_price": dk.get("matched_price"), "competitors": comps,
        })
    return out


def save_review(created_at: str, product: str, dk_matched: str | None,
                correct: bool, message: str | None, result: dict[str, Any] | None,
                run_id: int | None = None) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO reviews (created_at, product, dk_matched, correct, message, result, run_id) "
            "VALUES (?,?,?,?,?,?,?)",
            (created_at, product, dk_matched, 1 if correct else 0, message,
             json.dumps(result) if result is not None else None, run_id),
        )
        return int(cur.lastrowid)


def run_review_summary(run_id: int) -> dict[str, Any]:
    with _conn() as c:
        row = c.execute(
            "SELECT COUNT(*) total, COALESCE(SUM(correct),0) correct FROM reviews WHERE run_id=?",
            (run_id,),
        ).fetchone()
    total = int(row["total"]); correct = int(row["correct"])
    return {"reviewed": total, "correct": correct,
            "accuracy": round(100.0 * correct / total, 1) if total else None}


def list_reviews(limit: int = 1000, only_issues: bool = False) -> list[dict[str, Any]]:
    q = "SELECT id, created_at, product, dk_matched, correct, message FROM reviews"
    if only_issues:
        q += " WHERE correct = 0"
    q += " ORDER BY id DESC LIMIT ?"
    with _conn() as c:
        return [dict(r) for r in c.execute(q, (limit,)).fetchall()]


def review_summary() -> dict[str, Any]:
    with _conn() as c:
        row = c.execute(
            "SELECT COUNT(*) total, COALESCE(SUM(correct),0) correct FROM reviews"
        ).fetchone()
    total = int(row["total"]); correct = int(row["correct"])
    return {"total": total, "correct": correct,
            "accuracy": round(100.0 * correct / total, 1) if total else None}


# ── confirmed matches (the learned memory) ───────────────────────────────────
# Once a human confirms (or corrects) a product↔competitor match, we remember it
# keyed by the NORMALIZED dk name (caller supplies the key). On later runs the
# matcher trusts this instead of re-discovering. We store the LINK, not the price
# (price is re-scraped fresh each time), plus negative "no_match" truths.

# ── SHARED Neon connection, used ONLY for confirmed_matches ──────────────────
# One reused connection + keepalives/timeouts so a keep/hide read is snappy and a
# flaky network fails fast instead of hanging. Reads degrade to "no memory" on a DB
# error (the compare just does live discovery); writes propagate so the UI knows.
_pg_conn: Any = None
_pg_lock = threading.RLock()
_pg_ready = False

_PG_CONFIRMED_SCHEMA = """
CREATE TABLE IF NOT EXISTS confirmed_matches (
    id            bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    dk_key        text NOT NULL,
    competitor_id text NOT NULL,
    label         text NOT NULL,
    matched_url   text,
    matched_name  text,
    source        text,
    updated_at    text NOT NULL,
    UNIQUE(dk_key, competitor_id)
)
"""


@contextmanager
def _pgc() -> Any:
    global _pg_conn
    with _pg_lock:
        if _pg_conn is None or _pg_conn.closed:
            # NOTE: do NOT pass options="-c statement_timeout=..." — Neon's pooler
            # (PgBouncer) rejects server startup parameters ("unsupported startup
            # parameter"). keepalives/connect_timeout are client-side and fine.
            _pg_conn = psycopg.connect(
                get_settings().database_url, row_factory=dict_row,
                connect_timeout=12, keepalives=1, keepalives_idle=15,
                keepalives_interval=5, keepalives_count=3, tcp_user_timeout=15000)
        conn = _pg_conn
        try:
            with conn.transaction():
                yield conn
        except (psycopg.OperationalError, psycopg.InterfaceError):
            try:
                conn.close()
            except Exception:
                pass
            _pg_conn = None
            raise


def _ensure_confirmed() -> None:
    global _pg_ready
    if _pg_ready:
        return
    with _pg_lock:
        if _pg_ready:
            return
        with _pgc() as c:
            c.execute(_PG_CONFIRMED_SCHEMA)
            c.execute("CREATE INDEX IF NOT EXISTS ix_confirmed_key ON confirmed_matches(dk_key)")
        _pg_ready = True


def upsert_confirmed(dk_key: str, competitor_id: str, label: str,
                     matched_url: str | None, matched_name: str | None,
                     source: str, updated_at: str) -> None:
    """Remember a confirmed truth for (dk_key, competitor). label='correct' stores
    the confirmed url; label='no_match' remembers there is none. Re-confirming
    overwrites the previous answer. On the SHARED Neon DB → applies everywhere."""
    _ensure_confirmed()
    with _pgc() as c:
        c.execute(
            "INSERT INTO confirmed_matches "
            "(dk_key, competitor_id, label, matched_url, matched_name, source, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(dk_key, competitor_id) DO UPDATE SET "
            "label=excluded.label, matched_url=excluded.matched_url, "
            "matched_name=excluded.matched_name, source=excluded.source, "
            "updated_at=excluded.updated_at",
            (dk_key, competitor_id, label, matched_url, matched_name, source, updated_at),
        )


def clear_confirmed(dk_key: str, competitor_id: str) -> None:
    """Forget a confirmation (👎 / un-hide) so the matcher reverts to live discovery."""
    _ensure_confirmed()
    with _pgc() as c:
        c.execute("DELETE FROM confirmed_matches WHERE dk_key=%s AND competitor_id=%s",
                  (dk_key, competitor_id))


def get_confirmed(dk_key: str) -> dict[str, dict[str, Any]]:
    """All confirmed truths for a product → {competitor_id: {label, matched_url,
    matched_name, updated_at}}. Empty when nothing is remembered OR the shared DB is
    briefly unreachable (the compare then falls back to live discovery)."""
    try:
        _ensure_confirmed()
        with _pgc() as c:
            rows = c.execute(
                "SELECT competitor_id, label, matched_url, matched_name, updated_at "
                "FROM confirmed_matches WHERE dk_key=%s", (dk_key,)).fetchall()
        return {r["competitor_id"]: dict(r) for r in rows}
    except Exception:
        return {}


def confirmed_count() -> int:
    try:
        _ensure_confirmed()
        with _pgc() as c:
            return int(c.execute("SELECT COUNT(*) FROM confirmed_matches").fetchone()["count"])
    except Exception:
        return 0


def prune(retention_days: int) -> int:
    """Delete runs older than retention_days (by started_at). Returns #deleted."""
    with _conn() as c:
        cur = c.execute(
            "DELETE FROM runs WHERE started_at < datetime('now', ?)",
            (f"-{int(retention_days)} days",),
        )
        c.execute("DELETE FROM run_items WHERE run_id NOT IN (SELECT id FROM runs)")
        return cur.rowcount


def delete_run(run_id: int) -> bool:
    """Permanently delete one run and its items + reviews. Returns True if a run
    was removed."""
    with _conn() as c:
        cur = c.execute("DELETE FROM runs WHERE id=?", (run_id,))
        c.execute("DELETE FROM run_items WHERE run_id=?", (run_id,))
        c.execute("DELETE FROM reviews WHERE run_id=?", (run_id,))
        return cur.rowcount > 0
