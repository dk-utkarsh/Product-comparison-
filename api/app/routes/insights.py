"""Pricing insights endpoints — Overall (all runs, latest per product) and per-run."""
from __future__ import annotations

from fastapi import APIRouter

from app import insights, run_store

router = APIRouter(prefix="/insights", tags=["insights"])


@router.get("/overall")
def insights_overall() -> dict:
    """Buckets across ALL runs on this environment, de-duped to the latest result
    per product. Hidden competitors excluded."""
    items = insights.dedup_latest(run_store.all_run_items())
    return insights.compute(items, run_store.hidden_map(), run_store.kept_map())


@router.get("/run/{run_id}")
def insights_run(run_id: int) -> dict:
    """Buckets for a single run (bulk upload) only."""
    run = run_store.get_run(run_id)
    if run is None:
        return {"error": "run not found", "kpis": {}, "buckets": {}}
    items = [{"name": it.get("name"), "result": it.get("result"), "run_id": run_id}
             for it in run.get("items", []) if it.get("result")]
    out = insights.compute(items, run_store.hidden_map(), run_store.kept_map())
    out["run"] = {"id": run["id"], "started_at": run["started_at"],
                  "trigger": run.get("trigger")}
    return out
