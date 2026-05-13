"""
Weighted scoring + verdict mapping. Pure function — no I/O.

score = w_cosine * cosine + w_brand * brand + w_pack * pack + w_attr * attr
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.matching.attributes import Attributes
from app.settings import get_settings

_PACK_TOLERANCE = 0.02  # 2%


class Verdict(StrEnum):
    CONFIRMED = "confirmed"
    POSSIBLE = "possible"
    VARIANT = "variant"
    REJECTED = "rejected"


@dataclass(slots=True)
class MatchScore:
    score: float
    cosine: float
    brand_score: float
    pack_score: float
    attr_score: float
    verdict: Verdict


def _brand_score(s: Attributes, c: Attributes) -> float:
    if not s.brand:
        return 1.0
    return 1.0 if s.brand == c.brand else 0.0


def _pack_score(s: Attributes, c: Attributes) -> float:
    if s.pack_count is None or c.pack_count is None:
        return 1.0
    bigger = max(s.pack_count, c.pack_count)
    if bigger == 0:
        return 1.0
    diff = abs(s.pack_count - c.pack_count) / bigger
    return 1.0 if diff <= _PACK_TOLERANCE else max(0.0, 1.0 - diff)


def _attr_score(s: Attributes, c: Attributes) -> float:
    checks: list[float] = []
    for attr in ("iso_size", "shade", "concentration", "taper", "slot", "viscosity"):
        sv = getattr(s, attr)
        cv = getattr(c, attr)
        if sv is None or cv is None:
            continue
        checks.append(1.0 if sv == cv else 0.0)
    if s.model_codes and c.model_codes:
        checks.append(1.0 if set(s.model_codes) & set(c.model_codes) else 0.0)
    return sum(checks) / len(checks) if checks else 1.0


def score_match(
    cosine_sim: float,
    search_attrs: Attributes,
    candidate_attrs: Attributes,
) -> MatchScore:
    settings = get_settings()
    brand = _brand_score(search_attrs, candidate_attrs)
    pack = _pack_score(search_attrs, candidate_attrs)
    attr = _attr_score(search_attrs, candidate_attrs)

    score = (
        settings.score_w_cosine * cosine_sim
        + settings.score_w_brand * brand
        + settings.score_w_pack * pack
        + settings.score_w_attr * attr
    )

    if score >= settings.accept_threshold:
        verdict = Verdict.CONFIRMED
    elif score >= settings.possible_threshold:
        verdict = Verdict.POSSIBLE
    elif score >= settings.variant_threshold:
        verdict = Verdict.VARIANT
    else:
        verdict = Verdict.REJECTED

    return MatchScore(
        score=score,
        cosine=cosine_sim,
        brand_score=brand,
        pack_score=pack,
        attr_score=attr,
        verdict=verdict,
    )
