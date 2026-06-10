"""
Structured field-wise matcher (Approach A of the exact-match spec).

Compares two rich product records (name + description + packaging) and
returns CONFIRMED / BORDERLINE / REJECTED with reasons. BORDERLINE pairs
go on to the LLM judge; the others are final.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.matching.attributes import Attributes, extract_attributes_rich
from app.matching.embed import get_embedder
from app.matching.gates import gate_check
from app.matching.normalize import normalize_for_match
from app.matching.tokens import fuzz_ratio, weighted_overlap
from app.settings import get_settings


class StructuredVerdict(StrEnum):
    CONFIRMED = "confirmed"
    BORDERLINE = "borderline"
    REJECTED = "rejected"


@dataclass(slots=True)
class ProductRecord:
    """One side of a match — built from a scraped PDP (rich) or a search
    result (thin). Empty description/packaging just means fewer signals."""
    name: str
    url: str = ""
    description: str = ""
    packaging: str = ""
    price: float = 0.0
    mrp: float = 0.0
    pack_size: int = 1
    unit_price: float = 0.0
    sku: str | None = None
    source: str = ""


@dataclass(slots=True)
class MatchFeatures:
    """Feature vector — kept flat so it can later train the light classifier."""
    cosine: float = 0.0
    token_overlap: float = 0.0
    fuzz: float = 0.0
    brand_match: bool | None = None  # None = brand unknown on a side
    attrs_compared: int = 0          # variant attrs present on BOTH sides
    pack_ratio: float = 1.0
    unit_price_ratio: float | None = None
    thin_data: bool = False          # a side had no description/packaging


@dataclass(slots=True)
class StructuredResult:
    verdict: StructuredVerdict
    features: MatchFeatures
    reasons: list[str] = field(default_factory=list)
    pack_note: str | None = None


_VARIANT_FIELDS: tuple[str, ...] = (
    "iso_size", "shade", "concentration", "taper", "slot",
    "viscosity", "material", "dimension", "wire_form",
)


def _brand_conflict(s_attrs: Attributes, c_attrs: Attributes,
                    s_name: str, c_name: str) -> bool:
    """First-token brands differ AND neither brand appears anywhere in the
    other side's name. The containment check saves 'GC Fuji IX' vs
    'Fuji IX GP by GC' from a false reject."""
    sb, cb = s_attrs.brand, c_attrs.brand
    if not sb or not cb or sb == cb:
        return False
    return sb not in c_name.lower() and cb not in s_name.lower()


def structured_match(search: ProductRecord, candidate: ProductRecord) -> StructuredResult:
    s_norm = normalize_for_match(search.name)
    c_norm = normalize_for_match(candidate.name)
    if not s_norm or not c_norm:
        return StructuredResult(
            StructuredVerdict.REJECTED, MatchFeatures(), ["empty name"])

    gate = gate_check(s_norm, c_norm)
    if not gate.passed:
        return StructuredResult(
            StructuredVerdict.REJECTED, MatchFeatures(), [gate.reason])

    s_attrs = extract_attributes_rich(search.name, search.description, search.packaging)
    c_attrs = extract_attributes_rich(candidate.name, candidate.description, candidate.packaging)

    if _brand_conflict(s_attrs, c_attrs, search.name, candidate.name):
        return StructuredResult(
            StructuredVerdict.REJECTED, MatchFeatures(brand_match=False),
            [f"brand conflict: {s_attrs.brand} vs {c_attrs.brand}"])

    # Hard rule: a variant attribute explicitly present on BOTH sides and
    # different means different variant. A2 != A3, .016 != .018.
    mismatches: list[str] = []
    compared = 0
    for f_name in _VARIANT_FIELDS:
        sv = getattr(s_attrs, f_name)
        cv = getattr(c_attrs, f_name)
        if sv is None or cv is None:
            continue
        compared += 1
        if sv != cv:
            mismatches.append(f"{f_name} mismatch: {sv} vs {cv}")
    if mismatches:
        return StructuredResult(
            StructuredVerdict.REJECTED,
            MatchFeatures(attrs_compared=compared), mismatches)

    embedder = get_embedder()
    vecs = embedder.encode_many([s_norm, c_norm])
    cosine = float(vecs[0] @ vecs[1])
    tok = weighted_overlap(s_norm, c_norm)
    fzr = fuzz_ratio(s_norm, c_norm)

    pack_note: str | None = None
    pack_ratio = 1.0
    if search.pack_size != candidate.pack_size and search.pack_size > 0 and candidate.pack_size > 0:
        pack_note = f"{search.pack_size}/pack vs {candidate.pack_size}/pack"
        pack_ratio = candidate.pack_size / search.pack_size

    settings = get_settings()
    unit_ratio: float | None = None
    in_band = True
    s_unit = search.unit_price or search.price
    c_unit = candidate.unit_price or candidate.price
    if s_unit > 0 and c_unit > 0:
        unit_ratio = c_unit / s_unit
        max_ratio = settings.price_band_max_ratio
        in_band = (1.0 / max_ratio) <= unit_ratio <= max_ratio

    thin = not (search.description or search.packaging) or not (
        candidate.description or candidate.packaging)

    features = MatchFeatures(
        cosine=cosine, token_overlap=tok, fuzz=fzr,
        brand_match=(s_attrs.brand == c_attrs.brand) if s_attrs.brand and c_attrs.brand else None,
        attrs_compared=compared, pack_ratio=pack_ratio,
        unit_price_ratio=unit_ratio, thin_data=thin,
    )
    reasons = [
        f"cosine={cosine:.3f}", f"token={tok:.2f}", f"fuzz={fzr:.2f}",
        f"attrs_compared={compared}",
    ]
    if unit_ratio is not None:
        reasons.append(f"unit_price_ratio={unit_ratio:.2f}")

    strong_line = cosine >= settings.confirm_cosine or fzr >= settings.confirm_fuzz
    brand_ok = features.brand_match is not False
    # Thin data (no description/packaging on a side) normally blocks CONFIRMED,
    # but near-identical names with an agreeing variant attr are safe anyway.
    data_ok = (not thin) or (compared >= 1 and fzr >= 0.95)
    if strong_line and brand_ok and in_band and data_ok and (compared >= 1 or cosine >= 0.85):
        return StructuredResult(StructuredVerdict.CONFIRMED, features, reasons, pack_note)

    if not in_band:
        reasons.append("unit price outside band")
    if thin:
        reasons.append("thin data on one side")
    return StructuredResult(StructuredVerdict.BORDERLINE, features, reasons, pack_note)
