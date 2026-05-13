import pytest
from pydantic import ValidationError

from app.schemas import MatchRequest, MatchResponse, RankedCandidate


def test_match_request_requires_search_and_candidates():
    req = MatchRequest(search="3M Filtek", candidates=["3M Filtek Z350"])
    assert req.search == "3M Filtek"
    assert req.candidates == ["3M Filtek Z350"]


def test_match_request_rejects_empty_search():
    with pytest.raises(ValidationError):
        MatchRequest(search="", candidates=["x"])


def test_match_request_rejects_empty_candidates():
    with pytest.raises(ValidationError):
        MatchRequest(search="x", candidates=[])


def test_ranked_candidate_shape():
    rc = RankedCandidate(
        candidate="3M Filtek Z350",
        verdict="confirmed",
        score=0.92,
        cosine=0.95,
        reasons=["cosine=0.95"],
    )
    assert rc.verdict == "confirmed"


def test_match_response_keeps_candidates_sorted():
    resp = MatchResponse(
        ranked=[
            RankedCandidate(candidate="b", verdict="confirmed", score=0.9, cosine=0.9, reasons=[]),
            RankedCandidate(candidate="a", verdict="possible", score=0.6, cosine=0.6, reasons=[]),
        ]
    )
    assert resp.ranked[0].score >= resp.ranked[1].score
