from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Verdict = Literal["confirmed", "possible", "variant", "rejected"]


class MatchRequest(BaseModel):
    search: str = Field(min_length=1)
    candidates: list[str] = Field(min_length=1)

    @field_validator("candidates")
    @classmethod
    def _no_empty(cls, v: list[str]) -> list[str]:
        cleaned = [c for c in v if c and c.strip()]
        if not cleaned:
            raise ValueError("at least one non-empty candidate required")
        return cleaned


class RankedCandidate(BaseModel):
    candidate: str
    verdict: Verdict
    score: float
    cosine: float
    reasons: list[str]


class MatchResponse(BaseModel):
    ranked: list[RankedCandidate]
