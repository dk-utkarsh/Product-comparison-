"""
Top-level matching orchestrator.

Flow per (search, candidate) pair:
  1. normalize both
  2. hard gates -> reject if any conflict
  3. embed both -> cosine similarity
  4. extract attributes
  5. weighted score -> verdict
"""
from __future__ import annotations

from dataclasses import dataclass

from app.matching.attributes import extract_attributes
from app.matching.embed import get_embedder
from app.matching.gates import gate_check
from app.matching.normalize import normalize_for_match
from app.matching.score import MatchScore, Verdict, score_match


@dataclass(slots=True)
class TriageResult:
    verdict: Verdict
    score: float
    cosine: float
    reasons: list[str]


def triage(search: str, candidate: str) -> TriageResult:
    s_norm = normalize_for_match(search)
    c_norm = normalize_for_match(candidate)

    if not s_norm or not c_norm:
        return TriageResult(Verdict.REJECTED, 0.0, 0.0, ["empty string"])

    gate = gate_check(s_norm, c_norm)
    if not gate.passed:
        return TriageResult(Verdict.REJECTED, 0.0, 0.0, [gate.reason])

    embedder = get_embedder()
    vecs = embedder.encode_many([s_norm, c_norm])
    cosine = float(vecs[0] @ vecs[1])

    s_attrs = extract_attributes(s_norm)
    c_attrs = extract_attributes(c_norm)

    ms: MatchScore = score_match(cosine, s_attrs, c_attrs)

    return TriageResult(
        verdict=ms.verdict,
        score=ms.score,
        cosine=cosine,
        reasons=[
            f"cosine={cosine:.3f}",
            f"brand={ms.brand_score:.0f}",
            f"pack={ms.pack_score:.2f}",
            f"attr={ms.attr_score:.2f}",
        ],
    )
