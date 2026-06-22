"""
Lightweight asyncio scheduler for daily SKU-comparison runs.

No external dependency: a background task computes the next configured IST
run-time, sleeps until then, executes a run, and repeats. A run pulls N random
SKUs from the admin catalog, compares each through the normal pipeline, and
stores every CompareResult in SQLite so the UI can show the history.

Designed for an always-on server: the in-process loop fires for the lifetime of
the app process. Restarts simply resume at the next scheduled time (no
catch-up of missed slots, no run-on-boot).
"""
from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import structlog

from app import run_store
from app.matching.llm_judge import JudgeBudget
from app.settings import get_settings

log = structlog.get_logger()

_RUN_CONCURRENCY = 4
_task: asyncio.Task | None = None


def _parse_times(spec: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            h, m = chunk.split(":")
            out.append((int(h), int(m)))
        except ValueError:
            continue
    return sorted(set(out))


def _next_fire(now: datetime, times: list[tuple[int, int]]) -> datetime:
    """Soonest configured time strictly after `now` (today, else tomorrow)."""
    candidates = [now.replace(hour=h, minute=m, second=0, microsecond=0) for h, m in times]
    future = [c for c in candidates if c > now]
    if future:
        return min(future)
    first = min(candidates) if candidates else now.replace(hour=0, minute=0)
    return first + timedelta(days=1)


async def execute_run(trigger: str = "manual") -> int:
    """Run one batch: random SKUs → compare each → persist. Returns run_id."""
    # Imported lazily to avoid an import cycle (routes.compare imports heavy deps).
    from app.dk_admin import fetch_random_skus
    from app.db import get_db
    from app.routes.compare import _compare_one, DkRow

    s = get_settings()
    run_store.init_db()
    run_store.prune(s.runs_retention_days)

    tz = ZoneInfo(s.scheduled_run_tz)
    started = datetime.now(tz).isoformat(timespec="seconds")
    products = await fetch_random_skus(s.scheduled_skus_per_run)
    run_id = run_store.create_run(started, trigger, len(products))
    log.info("scheduled-run start", run_id=run_id, trigger=trigger, skus=len(products))

    db = None
    try:
        db = await get_db()
    except Exception:
        db = None
    budget = JudgeBudget(s.llm_judge_budget_per_run)
    sem = asyncio.Semaphore(_RUN_CONCURRENCY)

    async def one(idx: int, prod) -> None:
        async with sem:
            try:
                result = await _compare_one(DkRow(name=prod.name), db, budget)
                run_store.save_item(run_id, idx, prod.sku, prod.name, result.model_dump())
            except Exception as e:  # one bad SKU must not kill the run
                log.warning("run item failed", run_id=run_id, sku=prod.sku, error=str(e))

    try:
        await asyncio.gather(*(one(i, p) for i, p in enumerate(products)))
        status, err = "done", None
    except Exception as e:
        status, err = "error", str(e)
        log.error("scheduled-run failed", run_id=run_id, error=str(e))
    finally:
        if db is not None:
            with contextlib.suppress(Exception):
                await db.close()
    run_store.finish_run(run_id, datetime.now(tz).isoformat(timespec="seconds"), status, err)
    log.info("scheduled-run done", run_id=run_id, status=status)
    return run_id


async def _loop() -> None:
    s = get_settings()
    times = _parse_times(s.scheduled_run_times)
    tz = ZoneInfo(s.scheduled_run_tz)
    if not times:
        log.warning("scheduler: no valid run times configured")
        return
    log.info("scheduler started", times=times, tz=s.scheduled_run_tz)
    while True:
        now = datetime.now(tz)
        nxt = _next_fire(now, times)
        delay = max(1.0, (nxt - now).total_seconds())
        log.info("scheduler sleeping", next_run=nxt.isoformat(timespec="minutes"), seconds=int(delay))
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise
        try:
            await execute_run("scheduled")
        except Exception as e:  # never let one failure kill the loop
            log.error("scheduler run error", error=str(e))


def start_scheduler() -> None:
    global _task
    s = get_settings()
    run_store.init_db()
    if not s.scheduled_runs_enabled:
        log.info("scheduler disabled (scheduled_runs_enabled=false)")
        return
    if not s.dk_admin_api_key:
        log.warning("scheduler enabled but no dk_admin_api_key — not starting")
        return
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())


def stop_scheduler() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
