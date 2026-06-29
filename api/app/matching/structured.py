"""
Structured field-wise matcher (Approach A of the exact-match spec).

Compares two rich product records (name + description + packaging) and
returns CONFIRMED / BORDERLINE / REJECTED with reasons. BORDERLINE pairs
go on to the LLM judge; the others are final.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from app.matching.attributes import Attributes, extract_attributes_rich
from app.matching.embed import get_embedder
from app.matching.gates import _BRAND_ALIASES, gate_check
from app.matching.normalize import normalize_for_match
from app.matching.tokens import fuzz_ratio, weighted_overlap
from app.matching.variant_spec import SpecMatch, VariantSpec
from app.matching.variant_spec import compare as compare_spec
from app.matching.variant_spec import describe as describe_spec
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
    variant_spec: VariantSpec | None = None


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
    spec_match: str | None = None  # exact | same-tier | different-size | None


_VARIANT_FIELDS: tuple[str, ...] = (
    "iso_size", "shade", "concentration", "taper", "slot",
    "viscosity", "material", "dimension", "wire_form", "tip_number",
)


_STRONG_CODE_RE = re.compile(r"[a-z]{1,4}-?\d{3,}[a-z]{0,2}")

# Product-KIND words. A storage CONTAINER (box/case/stand…) is a different
# product from the multi-item BUNDLE that holds it (kit/set/system…). When two
# otherwise near-identical names disagree on kind, that "slight" name difference
# is actually decisive — so we look past the high token/fuzz score.
_CONTAINER_WORDS = frozenset({
    "box", "boxes", "case", "casing", "pouch", "stand", "holder", "organizer",
    "organiser", "caddy", "rack", "cassette", "container",
})
_BUNDLE_WORDS = frozenset({
    "kit", "kits", "set", "sets", "system", "combo", "package", "assortment", "bundle",
})


def _kind_mismatch(a_norm: str, b_norm: str) -> bool:
    """True when one name is purely a CONTAINER and the other purely a BUNDLE —
    e.g. 'Julldent Zygo Box' (a storage box) vs 'Julldent Zygo kit' (the full
    surgical kit that includes a box). Both having a container word, or both a
    bundle word, is NOT a mismatch."""
    a, b = set(a_norm.split()), set(b_norm.split())
    a_c, a_b = bool(a & _CONTAINER_WORDS), bool(a & _BUNDLE_WORDS)
    b_c, b_b = bool(b & _CONTAINER_WORDS), bool(b & _BUNDLE_WORDS)
    return (a_c and not a_b and b_b and not b_c) or (b_c and not b_b and a_b and not a_c)


def _main_model_codes(name: str) -> set[str]:
    """Strong manufacturer model codes in the MAIN name (parenthetical DK SKUs
    dropped) — letter prefix + 3+ digits, e.g. 'dl-300', 's6000'."""
    main = re.sub(r"\([^)]*\)", " ", name).lower()
    return {m.group(0) for m in _STRONG_CODE_RE.finditer(main)}


def _brands_aliased(a: str, b: str) -> bool:
    """True when two brand tokens are known equivalents (manufacturer ⇄ line),
    e.g. 'kidsecrown' ⇄ 'shinhung'. Mirrors the gate's _BRAND_ALIASES so this
    deeper check doesn't reject a same-brand product the gate already accepted."""
    for x, y in ((a, b), (b, a)):
        for alias in _BRAND_ALIASES.get(x, ()):
            if y == alias or y in alias.split() or y == alias.replace(" ", ""):
                return True
    return False


def _brand_conflict(s_attrs: Attributes, c_attrs: Attributes,
                    s_name: str, c_name: str) -> bool:
    """First-token brands differ AND neither brand appears anywhere in the
    other side's name. The containment check saves 'GC Fuji IX' vs
    'Fuji IX GP by GC' from a false reject; the alias check saves a manufacturer/
    line pair like 'Kids-e-Crown' vs 'Shinhung …'."""
    sb, cb = s_attrs.brand, c_attrs.brand
    if not sb or not cb or sb == cb or _brands_aliased(sb, cb):
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

    # A manufacturer model code in the DK product's MAIN name (e.g. "DL-300",
    # "S6000" — letter prefix + 3+ digits, NOT a trailing parenthetical DK SKU
    # like "(S5083)") that's absent from the competitor candidate means a
    # different model (Upcera DL-300 ≠ "Upcera P2 Plus 3D Intraoral Scanner").
    s_codes = _main_model_codes(search.name)
    if s_codes and not any(c in candidate.name.lower() for c in s_codes):
        return StructuredResult(
            StructuredVerdict.REJECTED, MatchFeatures(),
            [f"model code {sorted(s_codes)[0]!r} absent"])

    # Sub-variant / composition check. The "Extra" formulation line is a
    # genuinely different product — never match it to the non-Extra line, even
    # when the base names are identical.
    spec_match: str | None = None
    if search.variant_spec is not None and candidate.variant_spec is not None:
        sm_enum = compare_spec(search.variant_spec, candidate.variant_spec)
        spec_match = sm_enum.value
        if sm_enum is SpecMatch.DIFFERENT_FORMULATION:
            return StructuredResult(
                StructuredVerdict.REJECTED, MatchFeatures(),
                [
                    "different formulation: "
                    f"{describe_spec(search.variant_spec)} vs "
                    f"{describe_spec(candidate.variant_spec)}"
                ],
                spec_match=spec_match,
            )

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
    # Terse listings (a competitor names a product as just "Dental Avenue
    # Avuecal" with the form/size only in the PDP body) score low on name-only
    # cosine even when they're the same product. Let each side's description
    # contribute to the SEMANTIC signal — a bounded slice, and we take the MAX
    # with the name-only cosine so it can only strengthen a true match, never
    # weaken one. The brand / model-code / spec / price gates above still guard
    # against false positives. General: helps any sparse competitor listing.
    s_aug = normalize_for_match(f"{search.name} {search.description[:240]}") if search.description else ""
    c_aug = normalize_for_match(f"{candidate.name} {candidate.description[:240]}") if candidate.description else ""
    use_aug = bool(s_aug and c_aug and (s_aug != s_norm or c_aug != c_norm))
    texts = [s_norm, c_norm] + ([s_aug, c_aug] if use_aug else [])
    vecs = embedder.encode_many(texts)
    cosine = float(vecs[0] @ vecs[1])
    if use_aug:
        cosine = max(cosine, float(vecs[2] @ vecs[3]))
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

    # Same product, different size (e.g. DK 15g vs competitor 5g). Still a valid
    # match — surface the size delta so the cell can show a per-unit price.
    if spec_match == SpecMatch.DIFFERENT_SIZE.value:
        pack_note = (
            f"{describe_spec(search.variant_spec)} vs "
            f"{describe_spec(candidate.variant_spec)}"
        )
        reasons.append(f"different size: {pack_note}")
    elif spec_match in (SpecMatch.EXACT.value, SpecMatch.SAME_TIER.value):
        reasons.append(f"spec {spec_match}")

    # ── Look deeper than the NAME when it differs only slightly ──────────────
    # A high token/fuzz score is not enough on its own: two near-identical names
    # can be different products. Probe the discriminators that a surface name
    # match hides — the product KIND (container vs bundle) and the per-unit price
    # (a gap too large to be a pack/form difference). Either, uncorroborated by an
    # agreeing spec/attribute, means it's a different product, not a match.
    hard = settings.price_band_hard_ratio
    extreme_price = unit_ratio is not None and not (1.0 / hard <= unit_ratio <= hard)
    kind_clash = _kind_mismatch(s_norm, c_norm)
    corroborated = compared >= 1 or spec_match in (
        SpecMatch.EXACT.value, SpecMatch.SAME_TIER.value)
    # A near-exact NAME match (same brand, line, size tokens) is the same product
    # even when the price/unit band fails — that gap is almost always a pack / FORM
    # / bundled-freebie difference (a 25 Mtr reel vs a single, a mis-parsed "8 Tips
    # Free" pack, a competitor's bundle), NOT a different product. Computed here so
    # the hard-reject below can spare these and let the displayed price Δ + the ⚠
    # review flag do their job. WEAK-name lookalikes (low fuzz AND low token) are
    # still hard-rejected on the price band.
    near_exact = fzr >= settings.confirm_fuzz or tok >= 0.60
    if (kind_clash and not in_band) or (extreme_price and not corroborated and not near_exact):
        why = ("container vs kit/bundle" if kind_clash
               else f"per-unit price {unit_ratio:.1f}x apart")
        reasons.append(f"different product: {why}")
        return StructuredResult(
            StructuredVerdict.REJECTED, features, reasons, pack_note, spec_match=spec_match
        )

    strong_line = cosine >= settings.confirm_cosine or fzr >= settings.confirm_fuzz
    brand_ok = features.brand_match is not False
    price_ok = in_band or near_exact
    # Thin data (no description/packaging on a side) normally blocks CONFIRMED,
    # but near-identical names with an agreeing variant attr are safe anyway.
    data_ok = (not thin) or (compared >= 1 and fzr >= 0.95)
    # An exact composition match is itself strong evidence the data is sound.
    if spec_match == SpecMatch.EXACT.value:
        data_ok = True
    if strong_line and brand_ok and price_ok and data_ok and (
        compared >= 1 or cosine >= 0.85 or near_exact
    ):
        return StructuredResult(
            StructuredVerdict.CONFIRMED, features, reasons, pack_note, spec_match=spec_match
        )

    if not in_band:
        reasons.append("unit price outside band")
    if thin:
        reasons.append("thin data on one side")
    return StructuredResult(
        StructuredVerdict.BORDERLINE, features, reasons, pack_note, spec_match=spec_match
    )
