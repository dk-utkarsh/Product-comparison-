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


@router.get("/history")
def price_history(name: str) -> dict:
    """Past price points for a product (by exact name) across stored runs."""
    return {"name": name, "history": run_store.price_history(name)}


@router.get("/watchlist")
def watchlist() -> dict:
    """The fixed products kept in every run (for price tracking)."""
    return {"watchlist": run_store.get_watchlist()}


@router.post("/{run_id}/rerun")
async def rerun(run_id: int) -> dict:
    """Re-run the EXACT same products as a past run (to recompute & compare)."""
    run = run_store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    from app.dk_admin import AdminProduct
    products = [
        AdminProduct(sku=it.get("sku") or "", name=it["name"])
        for it in run.get("items", []) if it.get("name")
    ]
    if not products:
        raise HTTPException(status_code=400, detail="run has no products to replay")
    asyncio.create_task(execute_run("rerun", products=products, source_run_id=run_id))
    return {"status": "started", "products": len(products), "source_run_id": run_id}


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
