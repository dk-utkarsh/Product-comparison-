"""
Scheduled-run history API.

GET  /runs          → list run summaries (newest first)
GET  /runs/{id}     → one run + every SKU's CompareResult
POST /runs/trigger  → kick off a run now (manual), returns immediately
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from app import run_store
from app.scheduler import execute_run

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("")
def list_runs() -> dict:
    return {"runs": run_store.list_runs()}


@router.get("/{run_id}")
def get_run(run_id: int) -> dict:
    run = run_store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@router.post("/trigger")
async def trigger_run() -> dict:
    # Fire-and-forget so the request returns immediately; the UI polls /runs.
    asyncio.create_task(execute_run("manual"))
    return {"status": "started"}
