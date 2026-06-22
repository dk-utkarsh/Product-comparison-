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
    trigger     TEXT NOT NULL,          -- scheduled | manual
    sku_count   INTEGER DEFAULT 0,
    done_count  INTEGER DEFAULT 0,
    error       TEXT
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


def init_db() -> None:
    with _conn() as c:
        c.executescript(_SCHEMA)


def create_run(started_at: str, trigger: str, sku_count: int) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO runs (started_at, status, trigger, sku_count) VALUES (?,?,?,?)",
            (started_at, "running", trigger, sku_count),
        )
        return int(cur.lastrowid)


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
    return [dict(r) for r in rows]


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
    out["items"] = [
        {"idx": it["idx"], "sku": it["sku"], "name": it["name"],
         "result": json.loads(it["result"]) if it["result"] else None}
        for it in items
    ]
    return out


def prune(retention_days: int) -> int:
    """Delete runs older than retention_days (by started_at). Returns #deleted."""
    with _conn() as c:
        cur = c.execute(
            "DELETE FROM runs WHERE started_at < datetime('now', ?)",
            (f"-{int(retention_days)} days",),
        )
        c.execute("DELETE FROM run_items WHERE run_id NOT IN (SELECT id FROM runs)")
        return cur.rowcount
