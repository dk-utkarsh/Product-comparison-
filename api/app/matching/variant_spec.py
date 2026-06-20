"""
Sub-variant / composition spec — Python side.

Specs are PARSED in TypeScript (lib/variant-spec.ts) and arrive on each scraped
record as a `variantSpec` dict (and per-variant on `variants[]`). This module
just models that dict and compares a competitor spec against the Dentalkart
(source-of-truth) spec so the matcher can:

  * never cross the "Extra" formulation line (a different product), and
  * prefer the same size, falling back to the closest size with a per-unit price.

Keep the comparison logic in sync with compareSpecToTruth() in the TS module.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

_REL_TOL = 0.05  # 5% — tolerate "13.1g" vs "13g" rounding across sites.


class SpecMatch(StrEnum):
    EXACT = "exact"  # same formulation + same measured size
    SAME_TIER = "same-tier"  # same formulation + same Big/Mini tier, grams differ
    DIFFERENT_SIZE = "different-size"  # same formulation, different size
    DIFFERENT_FORMULATION = "different-formulation"  # Extra vs non-Extra → never match
    UNKNOWN = "unknown"  # not enough signal to decide


@dataclass(slots=True)
class VariantSpec:
    powder_g: float | None = None
    liquid_g: float | None = None
    liquid_ml: float | None = None
    capsules: float | None = None
    pieces: float | None = None
    is_extra: bool = False
    size_tier: str | None = None  # "big" | "mini"
    kit_tier: str | None = None  # only | set | basic | basic plus | premium | ...
    torque: str | None = None  # "torque" | "non-torque"
    raw: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "VariantSpec | None":
        if not isinstance(d, dict):
            return None
        return cls(
            powder_g=_num(d.get("powderG")),
            liquid_g=_num(d.get("liquidG")),
            liquid_ml=_num(d.get("liquidMl")),
            capsules=_num(d.get("capsules")),
            pieces=_num(d.get("pieces")),
            is_extra=bool(d.get("isExtra", False)),
            size_tier=d.get("sizeTier") or None,
            kit_tier=d.get("kitTier") or None,
            torque=d.get("torque") or None,
            raw=str(d.get("raw", "")),
        )


def config_from_text(name: str) -> tuple[str | None, str | None, bool]:
    """Extract configuration discriminators (kit_tier, torque, is_extra) from a
    raw product name — used to match an input/xlsx name to the right grouped
    child on the Dentalkart side (parsing proper happens in TS; this is the
    minimal mirror needed Python-side). Keep in sync with lib/variant-spec.ts."""
    low = name.lower()
    if re.search(r"\bpremium\b", low):
        kit: str | None = "premium"
    elif re.search(r"\bbasic\s*\+?\s*plus\b", low) or re.search(r"\bbasic\s*\+", low):
        kit = "basic plus"
    elif re.search(r"\bbasic\b", low):
        kit = "basic"
    elif re.search(r"\bdeluxe\b", low):
        kit = "deluxe"
    elif re.search(r"\bstandard\b", low):
        kit = "standard"
    elif re.search(r"\bset\s+of\s+\d+\b", low):
        kit = "set"
    elif re.search(r"\bonly\b", low):
        kit = "only"
    else:
        kit = None
    if re.search(r"\bnon[\s-]?torque\b", low):
        torque: str | None = "non-torque"
    elif re.search(r"\btorque\b", low):
        torque = "torque"
    else:
        torque = None
    return kit, torque, bool(re.search(r"\bextra\b", low))


_CONFIG_WORDS = re.compile(
    r"\b(premium|basic\s*\+?\s*plus|basic|standard|deluxe|extra|only|"
    r"non[\s-]?torque|torque|ratchet)\b",
    re.I,
)


def base_name(name: str) -> str:
    """Strip variant specifics (config tier, torque, Extra, set-of-N, sizes,
    measurements, inline codes) to get the grouped product's base name.
    Dentalkart indexes grouped products under this base, so searching it
    reliably surfaces the parent even when the input is a specific child name —
    e.g. 'Jull-Dent 79C Premium Small Orringer Retractor -40mm' → 'JullDent
    Orringer Retractor', which DK search resolves to the parent."""
    s = re.sub(r"\bset\s+of\s+\d+\b", " ", name, flags=re.I)
    s = re.sub(r"\bdlc\s+coated\s+drills?\b", " ", s, flags=re.I)
    # measurements / sizes
    s = re.sub(
        r"\b\d+(?:\.\d+)?\s?(?:mm|cm|ml|mg|kg|gm?|microns?|µ|inch|inches|oz|sheets?)\b",
        " ", s, flags=re.I,
    )
    # size descriptors
    s = re.sub(r"\b(?:small|large|medium|mini|maxi|x-?small|x-?large|xs|xl)\b", " ", s, flags=re.I)
    # standalone alphanumeric codes (79C, 079C, A1928) — mixed letters+digits
    s = re.sub(r"\b(?=[a-z0-9]*\d)(?=[a-z0-9]*[a-z])[a-z0-9]{2,7}\b", " ", s, flags=re.I)
    s = _CONFIG_WORDS.sub(" ", s)
    s = re.sub(r"\bwith\b", " ", s, flags=re.I)
    # collapse an intra-word hyphen in the brand (Jull-Dent → JullDent), which
    # DK's search tokenizes better than the hyphenated form.
    s = re.sub(r"(?<=[A-Za-z])-(?=[A-Za-z])", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" -")
    return re.sub(r"\s+", " ", s)


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def has_size_signal(s: VariantSpec) -> bool:
    return (
        any(
            x is not None
            for x in (s.powder_g, s.liquid_g, s.liquid_ml, s.capsules, s.pieces)
        )
        or s.size_tier is not None
        or s.kit_tier is not None
        or s.torque is not None
    )


def _config_mismatch(a: VariantSpec, b: VariantSpec) -> bool:
    """Hard config mismatch — Extra line, kit tier, or torque differs. Different
    products; never cross-match."""
    if a.is_extra != b.is_extra:
        return True
    if a.kit_tier and b.kit_tier and a.kit_tier != b.kit_tier:
        return True
    if a.torque and b.torque and a.torque != b.torque:
        return True
    return False


def _approx(a: float | None, b: float | None) -> bool | None:
    if a is None or b is None:
        return None
    if a == 0 and b == 0:
        return True
    bigger = max(abs(a), abs(b)) or 1.0
    return abs(a - b) / bigger <= _REL_TOL


def _tier(s: VariantSpec) -> str | None:
    if s.size_tier:
        return s.size_tier
    if s.powder_g is not None:
        return "big" if s.powder_g >= 10 else "mini"
    if s.pieces is not None:
        return "big" if s.pieces >= 10 else "mini"
    return None


def compare(truth: VariantSpec, cand: VariantSpec) -> SpecMatch:
    """Compare a candidate spec against the Dentalkart truth spec."""
    # Configuration must agree — Extra line, kit tier, torque type.
    if _config_mismatch(truth, cand):
        return SpecMatch.DIFFERENT_FORMULATION

    if not has_size_signal(truth) or not has_size_signal(cand):
        return SpecMatch.UNKNOWN

    checks = [
        _approx(truth.powder_g, cand.powder_g),
        _approx(truth.liquid_g, cand.liquid_g),
        _approx(truth.liquid_ml, cand.liquid_ml),
        _approx(truth.capsules, cand.capsules),
        _approx(truth.pieces, cand.pieces),
        # Categorical config: present on both ⇒ equal here (mismatch already
        # returned above), so it's a positive exact signal.
        (True if truth.kit_tier and cand.kit_tier else None),
        (True if truth.torque and cand.torque else None),
    ]
    comparable = [c for c in checks if c is not None]

    if comparable:
        if all(comparable):
            return SpecMatch.EXACT
        tt, ct = _tier(truth), _tier(cand)
        if tt and ct:
            return SpecMatch.SAME_TIER if tt == ct else SpecMatch.DIFFERENT_SIZE
        return SpecMatch.DIFFERENT_SIZE

    tt, ct = _tier(truth), _tier(cand)
    if tt and ct:
        return SpecMatch.SAME_TIER if tt == ct else SpecMatch.DIFFERENT_SIZE
    return SpecMatch.UNKNOWN


def base_quantity(s: VariantSpec) -> tuple[float, str]:
    """Quantity used to normalize price to a per-unit basis (with its unit), so
    a 15g listing and a 5g listing (or pack-of-6 vs single) compare fairly."""
    if s.powder_g and s.powder_g > 0:
        return s.powder_g, "g powder"
    if s.capsules and s.capsules > 0:
        return s.capsules, "capsule"
    if s.pieces and s.pieces > 0:
        return s.pieces, "piece"
    if s.liquid_ml and s.liquid_ml > 0:
        return s.liquid_ml, "ml"
    return 1.0, "unit"


def describe(s: VariantSpec) -> str:
    parts: list[str] = []
    if s.is_extra:
        parts.append("Extra")
    if s.kit_tier:
        parts.append(s.kit_tier)
    if s.torque:
        parts.append(s.torque)
    if s.powder_g is not None:
        parts.append(f"{_fmt(s.powder_g)}g powder")
    if s.liquid_g is not None:
        parts.append(f"{_fmt(s.liquid_g)}g liquid")
    elif s.liquid_ml is not None:
        parts.append(f"{_fmt(s.liquid_ml)}ml liquid")
    if s.capsules is not None:
        parts.append(f"{_fmt(s.capsules)} capsules")
    if s.pieces is not None:
        parts.append(f"pack of {_fmt(s.pieces)}")
    if not parts and s.size_tier:
        parts.append(f"{s.size_tier} pack")
    return " + ".join(parts) or "(no size spec)"


def _fmt(n: float) -> str:
    return str(int(n)) if n == int(n) else str(n)
