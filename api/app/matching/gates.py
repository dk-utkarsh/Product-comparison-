"""
Hard-conflict gates. Port of lib/smart-matcher.ts conflict logic.

Each gate returns False (and a reason) when the two product names cannot
possibly be the same product. Composed in gate_check(), which is the
single entry point.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.matching.attributes import Attributes, extract_attributes

_INCOMPATIBLE_GROUPS: list[frozenset[str]] = [
    frozenset({
        "rongeur", "forceps", "elevator", "excavator", "explorer",
        "probe", "mirror", "retractor", "plugger", "spreader",
        "condenser", "scissors", "plier", "pliers", "cutter",
        "clamp", "tweezer", "scaler", "curette", "periotome",
        "gauge", "caliper", "file", "files",
        "handpiece", "bur", "burs",
    }),
    frozenset({"liquid", "powder", "gel", "paste", "capsule", "tablet"}),
    frozenset({
        "refill", "refills", "tip", "tips", "replacement", "spare",
        "cartridge", "adapter", "charger", "battery", "kit", "kits",
    }),
    frozenset({
        "motor", "scaler", "scanner", "camera", "autoclave",
        "chair", "stool", "monitor", "light",
    }),
    frozenset({
        "bracket", "brackets", "wire", "wires", "band", "bands",
        "elastic", "elastics", "archwire",
    }),
]

_WORD_TO_GROUP: dict[str, int] = {
    word: idx for idx, group in enumerate(_INCOMPATIBLE_GROUPS) for word in group
}

_CATEGORY_EXCLUSIONS: list[tuple[frozenset[str], frozenset[str]]] = [
    (
        frozenset({"monitor", "tft", "lcd", "screen", "display", "computer"}),
        frozenset({"crown", "crowns", "bracket", "dental"}),
    ),
    (frozenset({"conventional"}), frozenset({"mbt", "roth"})),
    (frozenset({"mbt"}), frozenset({"roth", "conventional", "duploslot"})),
    (frozenset({"roth"}), frozenset({"mbt", "conventional", "duploslot"})),
    (frozenset({"duploslot"}), frozenset({"standard", "mbt", "roth"})),
    (frozenset({"self-ligating"}), frozenset({"conventional"})),
]

_WORD_RE = re.compile(r"\b[a-z0-9]+\b")


@dataclass(slots=True)
class GateResult:
    passed: bool
    reason: str = ""


def _stem(w: str) -> str:
    """Collapse trivial plurals so refill/refills, tip/tips, brackets/bracket
    map to the same stem. Only handles the cases we care about for gate
    equivalence checks — not a general lemmatizer.
    """
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _words(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _word_boundary(text: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", text, re.IGNORECASE) is not None


def _brand_match(a: Attributes, search: str, found: str) -> bool:
    if not a.brand:
        return True
    return _word_boundary(found, a.brand)


def _incompatible_types(search_words: set[str], found_words: set[str]) -> bool:
    s_groups = {_WORD_TO_GROUP[w] for w in search_words if w in _WORD_TO_GROUP}
    f_groups = {_WORD_TO_GROUP[w] for w in found_words if w in _WORD_TO_GROUP}
    if not s_groups or not f_groups:
        return False
    s_words = {w for w in search_words if w in _WORD_TO_GROUP}
    f_words = {w for w in found_words if w in _WORD_TO_GROUP}
    for g in s_groups & f_groups:
        s_in_g = {_stem(w) for w in s_words if _WORD_TO_GROUP[w] == g}
        f_in_g = {_stem(w) for w in f_words if _WORD_TO_GROUP[w] == g}
        if s_in_g and f_in_g and not (s_in_g & f_in_g):
            return True
    return False


def _category_exclusion(search_words: set[str], found_words: set[str]) -> bool:
    for left, right in _CATEGORY_EXCLUSIONS:
        if (search_words & left and found_words & right) or (
            search_words & right and found_words & left
        ):
            return True
    return False


def gate_check(search: str, found: str) -> GateResult:
    s_attrs = extract_attributes(search)
    f_attrs = extract_attributes(found)

    if not _brand_match(s_attrs, search, found):
        return GateResult(False, f"brand mismatch: '{s_attrs.brand}' not in '{found}'")

    s_words = _words(search)
    f_words = _words(found)

    if _incompatible_types(s_words, f_words):
        return GateResult(False, "incompatible product types")

    if _category_exclusion(s_words, f_words):
        return GateResult(False, "category exclusion")

    if s_attrs.iso_size and f_attrs.iso_size and s_attrs.iso_size != f_attrs.iso_size:
        return GateResult(False, f"iso size mismatch: {s_attrs.iso_size} vs {f_attrs.iso_size}")

    if s_attrs.shade and f_attrs.shade and s_attrs.shade != f_attrs.shade:
        return GateResult(False, f"shade mismatch: {s_attrs.shade} vs {f_attrs.shade}")

    if (
        s_attrs.concentration is not None
        and f_attrs.concentration is not None
        and abs(s_attrs.concentration - f_attrs.concentration) > 1e-6
    ):
        return GateResult(False, "concentration mismatch")

    if s_attrs.taper and f_attrs.taper and s_attrs.taper != f_attrs.taper:
        return GateResult(False, "taper mismatch")

    if s_attrs.slot and f_attrs.slot and s_attrs.slot != f_attrs.slot:
        return GateResult(False, "slot mismatch")

    if (
        s_attrs.model_codes
        and f_attrs.model_codes
        and not (set(s_attrs.model_codes) & set(f_attrs.model_codes))
    ):
        return GateResult(False, "model code mismatch")

    if s_attrs.viscosity and f_attrs.viscosity and s_attrs.viscosity != f_attrs.viscosity:
        return GateResult(False, "viscosity mismatch")

    return GateResult(True)
