"""
Golden-set labeling. One row = a human-asserted truth: 'this Dentalkart
product's true link on <source> is <url>' or 'it has no match there'.
scripts/eval.py measures pipeline precision/recall against these rows.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.db import get_db

router = APIRouter(prefix="/golden", tags=["golden"])


class GoldenRequest(BaseModel):
    dk_name: str = Field(min_length=1)
    source: str = Field(min_length=1)
    competitor_url: str | None = None
    label: Literal["correct", "no_match"]


class GoldenResponse(BaseModel):
    status: Literal["ok"] = "ok"
    count: int


@router.post("", response_model=GoldenResponse)
async def post_golden(req: GoldenRequest) -> GoldenResponse:
    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO golden_links (dk_name, source, competitor_url, label)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (dk_name, source) DO UPDATE SET
              competitor_url = EXCLUDED.competitor_url,
              label = EXCLUDED.label, created_at = now()
            """,
            req.dk_name, req.source, req.competitor_url, req.label,
        )
        row = await db.fetchrow("SELECT count(*) AS c FROM golden_links")
        return GoldenResponse(count=int(row["c"]) if row else 0)
    finally:
        await db.close()


@router.get("/count")
async def golden_count() -> dict[str, int]:
    db = await get_db()
    try:
        row = await db.fetchrow("SELECT count(*) AS c FROM golden_links")
        return {"count": int(row["c"]) if row else 0}
    finally:
        await db.close()
