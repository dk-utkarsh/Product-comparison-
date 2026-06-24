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
async def rerun(run_id: int, serp: bool = False) -> dict:
    """Re-run the EXACT same products as a past run (to recompute & compare).
    `serp` replays them through the Google/SerpAPI path so the result can be
    diffed against the original standard run — quota-capped to 15 products."""
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
    if serp:
        products = products[:15]   # protect the ~100 searches/month quota
    asyncio.create_task(execute_run(
        "rerun-google" if serp else "rerun",
        products=products, source_run_id=run_id, use_serp=serp,
    ))
    return {"status": "started", "products": len(products), "source_run_id": run_id,
            "via": "google" if serp else "standard"}


@router.get("/{run_id}")
def get_run(run_id: int) -> dict:
    run = run_store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@router.delete("/{run_id}")
def delete_run(run_id: int) -> dict:
    """Permanently delete a run (and its items + reviews)."""
    if not run_store.delete_run(run_id):
        raise HTTPException(status_code=404, detail="run not found")
    return {"status": "deleted", "run_id": run_id}


@router.post("/trigger")
async def trigger_run(count: int | None = None, serp: bool = False) -> dict:
    # Fire-and-forget so the request returns immediately; the UI polls /runs.
    # `count` overrides the default run size (watchlist + random) for a one-off
    # test of a custom number of products. `serp` runs each product through the
    # Google/SerpAPI path — quota-limited, so the count is hard-capped small.
    if serp:
        n = max(1, min(count or 10, 15))   # protect the ~100 searches/month quota
    else:
        n = max(1, min(count, 200)) if count else None
    asyncio.create_task(execute_run("manual-google" if serp else "manual", count=n, use_serp=serp))
    return {"status": "started", "count": n, "via": "google" if serp else "standard"}
