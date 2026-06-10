"""
Top-level matching orchestrator.

Flow per (search, candidate) pair:
  1. normalize both
  2. hard gates -> reject on any conflict
  3. embed both -> cosine similarity
  4. extract attributes (regex)
  5. compute token-overlap (Jaccard with stopword weighting) and
     rapidfuzz token_set_ratio for precision signals
  6. weighted score -> verdict

`triage_batch` does the same work for N candidates against one search,
encoding everything in a single forward pass — much faster than calling
`triage` N times when N is large.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.matching.attributes import Attributes, extract_attributes
from app.matching.embed import get_embedder
from app.matching.gates import gate_check
from app.matching.normalize import normalize_for_match
from app.matching.score import MatchScore, Verdict, score_match
from app.matching.tokens import fuzz_ratio, weighted_overlap


@dataclass(slots=True)
class TriageResult:
    verdict: Verdict
    score: float
    cosine: float
    reasons: list[str]


def _reasons(cosine: float, ms: MatchScore) -> list[str]:
    return [
        f"cosine={cosine:.3f}",
        f"brand={ms.brand_score:.0f}",
        f"pack={ms.pack_score:.2f}",
        f"attr={ms.attr_score:.2f}",
        f"token={ms.token_score:.2f}",
        f"fuzz={ms.fuzz_score:.2f}",
    ]


def _score_pair(
    s_norm: str,
    c_norm: str,
    cosine: float,
    s_attrs: Attributes,
) -> TriageResult:
    c_attrs = extract_attributes(c_norm)
    tok = weighted_overlap(s_norm, c_norm)
    fzr = fuzz_ratio(s_norm, c_norm)
    ms: MatchScore = score_match(cosine, s_attrs, c_attrs, tok, fzr)
    return TriageResult(
        verdict=ms.verdict,
        score=ms.score,
        cosine=cosine,
        reasons=_reasons(cosine, ms),
    )


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
    return _score_pair(s_norm, c_norm, cosine, s_attrs)


def triage_batch(search: str, candidates: list[str]) -> list[TriageResult]:
    """Triage every candidate against one search, batch-encoding all of
    them in a single embedder call. Returns results in the same order as
    `candidates`."""
    if not candidates:
        return []

    s_norm = normalize_for_match(search)
    if not s_norm:
        return [
            TriageResult(Verdict.REJECTED, 0.0, 0.0, ["empty search"])
            for _ in candidates
        ]

    s_attrs = extract_attributes(s_norm)

    # Partition: gate-rejected candidates skip the embedder entirely.
    gate_results: list[tuple[str, str, str | None]] = []
    pending_idx: list[int] = []
    pending_norms: list[str] = []
    for i, cand in enumerate(candidates):
        c_norm = normalize_for_match(cand)
        if not c_norm:
            gate_results.append((cand, "", "empty candidate"))
            continue
        gate = gate_check(s_norm, c_norm)
        if not gate.passed:
            gate_results.append((cand, c_norm, gate.reason))
        else:
            gate_results.append((cand, c_norm, None))
            pending_idx.append(i)
            pending_norms.append(c_norm)

    # One encode call for the search + every survivor candidate.
    embedder = get_embedder()
    if pending_norms:
        vecs = embedder.encode_many([s_norm, *pending_norms])
        s_vec = vecs[0]
        cand_vecs = vecs[1:]
        cosines: dict[int, float] = {
            idx: float(np.dot(s_vec, cand_vecs[k]))
            for k, idx in enumerate(pending_idx)
        }
    else:
        cosines = {}

    out: list[TriageResult] = []
    for i, (_, c_norm, reject_reason) in enumerate(gate_results):
        if reject_reason is not None:
            out.append(TriageResult(Verdict.REJECTED, 0.0, 0.0, [reject_reason]))
            continue
        cosine = cosines.get(i, 0.0)
        out.append(_score_pair(s_norm, c_norm, cosine, s_attrs))
    return out
