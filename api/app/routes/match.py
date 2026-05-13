from __future__ import annotations

import heapq

from fastapi import APIRouter

from app.matching.triage import triage
from app.schemas import MatchRequest, MatchResponse, RankedCandidate

router = APIRouter()


@router.post("/match", response_model=MatchResponse)
def post_match(req: MatchRequest) -> MatchResponse:
    scored: list[tuple[float, RankedCandidate]] = []
    for cand in req.candidates:
        r = triage(req.search, cand)
        scored.append(
            (
                r.score,
                RankedCandidate(
                    candidate=cand,
                    verdict=r.verdict.value,
                    score=r.score,
                    cosine=r.cosine,
                    reasons=r.reasons,
                ),
            )
        )
    ranked = [rc for _, rc in heapq.nlargest(len(scored), scored, key=lambda x: x[0])]
    return MatchResponse(ranked=ranked)
