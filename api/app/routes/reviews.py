"""
Human accuracy review.

The UI lets a reviewer mark each result row correct (✓) or needs-fix (with an
improvement note). Submitting a batch stores each review and returns the
accuracy. Reviews are persisted (SQLite) AND logged so improvement notes are
easy to pull and act on at a root-cause level.

POST /reviews          → store a batch, return this-batch accuracy
GET  /reviews          → recent reviews (use ?only_issues=1 for just the fixes)
GET  /reviews/summary  → all-time accuracy
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter
from pydantic import BaseModel

from app import run_store
from app.matching.normalize import normalize_for_match
from app.settings import get_settings

log = structlog.get_logger()
router = APIRouter(prefix="/reviews", tags=["reviews"])


def _confirm_key(product: str) -> str:
    """Stable lookup key for the confirmed-match memory."""
    return normalize_for_match(product).lower().strip()


def _remember_confirmations(item: ReviewItem, now: str) -> int:
    """A ✓-correct row means every match SHOWN for it is right — remember each
    competitor's confirmed link so the matcher reuses it next time (we re-scrape
    the link for a fresh price). Only positive, page-backed matches are stored;
    'no match' / unverified cells are left to live discovery. Returns how many."""
    if not item.correct or not item.result:
        return 0
    key = _confirm_key(item.product)
    if not key:
        return 0
    stored = 0
    for c in (item.result.get("competitors") or []):
        cid = c.get("competitor_id")
        url = c.get("matched_url")
        # a genuine, page-verified shown match: has a url + price, no reject note
        if cid and url and c.get("matched_price") is not None and not c.get("note"):
            run_store.upsert_confirmed(
                key, cid, "correct", url, c.get("matched_name"), "review", now)
            stored += 1
    return stored


class ReviewItem(BaseModel):
    product: str
    dk_matched: str | None = None
    correct: bool
    message: str | None = None
    result: dict | None = None


class ReviewBatch(BaseModel):
    reviews: list[ReviewItem]
    run_id: int | None = None   # set when reviewing a scheduled run's results


@router.post("")
def submit_reviews(batch: ReviewBatch) -> dict:
    run_store.init_db()
    tz = ZoneInfo(get_settings().scheduled_run_tz)
    now = datetime.now(tz).isoformat(timespec="seconds")
    total = len(batch.reviews)
    correct = 0
    learned = 0
    for r in batch.reviews:
        if r.correct:
            correct += 1
            learned += _remember_confirmations(r, now)   # learn from this ✓
        else:
            # Surface improvement notes in the logs for root-cause work.
            log.warning("review-issue", product=r.product, dk_matched=r.dk_matched,
                        message=(r.message or "").strip())
        run_store.save_review(now, r.product, r.dk_matched, r.correct, r.message,
                              r.result, batch.run_id)
    accuracy = round(100.0 * correct / total, 1) if total else None
    log.info("review-batch", total=total, correct=correct, accuracy=accuracy,
             learned=learned, run_id=batch.run_id)
    return {"total": total, "correct": correct, "needs_fix": total - correct,
            "accuracy": accuracy, "learned": learned,
            "overall": run_store.review_summary()}


@router.get("")
def get_reviews(only_issues: bool = False) -> dict:
    return {"reviews": run_store.list_reviews(only_issues=only_issues)}


@router.get("/summary")
def get_summary() -> dict:
    return run_store.review_summary()
