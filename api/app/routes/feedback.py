"""
Match-feedback endpoints.

Lets the UI record whether a competitor match was the right product or not.
Every click stores one labelled row in `match_feedback`. Once enough rows
accumulate (~300+) the threshold + feature weights can be re-tuned from the
data instead of hand-picked.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.db import get_db

router = APIRouter(prefix="/feedback", tags=["feedback"])


class FeedbackRequest(BaseModel):
    search_term: str = Field(min_length=1)
    competitor_id: str = Field(min_length=1)
    matched_name: str = Field(min_length=1)
    matched_url: str | None = None
    matched_price: float | None = None
    dk_price: float | None = None
    score: float
    cosine: float | None = None
    verdict: str
    reasons: str | None = None
    was_correct: bool
    dk_url: str | None = None


class FeedbackResponse(BaseModel):
    status: Literal["ok"] = "ok"
    total: int


class FeedbackStats(BaseModel):
    total: int
    correct: int
    incorrect: int


@router.post("", response_model=FeedbackResponse)
async def post_feedback(req: FeedbackRequest) -> FeedbackResponse:
    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO match_feedback (
              search_term, competitor_id, matched_name, matched_url,
              matched_price, dk_price, score, cosine, verdict, reasons,
              was_correct
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            """,
            req.search_term,
            req.competitor_id,
            req.matched_name,
            req.matched_url,
            req.matched_price,
            req.dk_price,
            req.score,
            req.cosine,
            req.verdict,
            req.reasons,
            req.was_correct,
        )
        if req.dk_url and req.matched_url:
            from app.registry import set_link_status
            status = "human_verified" if req.was_correct else "killed"
            try:
                await set_link_status(
                    db, req.dk_url, req.competitor_id, req.matched_url, status)
            except Exception:  # feedback insert already landed
                pass
        row = await db.fetchrow("SELECT count(*) AS c FROM match_feedback")
        return FeedbackResponse(total=int(row["c"]) if row else 0)
    finally:
        await db.close()


@router.get("/stats", response_model=FeedbackStats)
async def feedback_stats() -> FeedbackStats:
    db = await get_db()
    try:
        row = await db.fetchrow(
            """
            SELECT
              count(*) AS total,
              count(*) FILTER (WHERE was_correct) AS correct,
              count(*) FILTER (WHERE NOT was_correct) AS incorrect
            FROM match_feedback
            """
        )
        if row is None:
            return FeedbackStats(total=0, correct=0, incorrect=0)
        return FeedbackStats(
            total=int(row["total"]),
            correct=int(row["correct"]),
            incorrect=int(row["incorrect"]),
        )
    finally:
        await db.close()
