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
from pathlib import Path
from typing import Any

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


def prune(retention_days: int) -> int:
    """Delete runs older than retention_days (by started_at). Returns #deleted."""
    with _conn() as c:
        cur = c.execute(
            "DELETE FROM runs WHERE started_at < datetime('now', ?)",
            (f"-{int(retention_days)} days",),
        )
        c.execute("DELETE FROM run_items WHERE run_id NOT IN (SELECT id FROM runs)")
        return cur.rowcount
