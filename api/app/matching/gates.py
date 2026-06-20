"""
Hard-conflict gates. Port of lib/smart-matcher.ts conflict logic.

Each gate returns False (and a reason) when the two product names cannot
possibly be the same product. Composed in gate_check(), which is the
single entry point.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.matching.attributes import Attributes, extract_attributes, extract_brand
from app.matching.tokens import distinguishing_tokens

_INCOMPATIBLE_GROUPS: list[frozenset[str]] = [
    frozenset({
        "rongeur", "forceps", "elevator", "excavator", "explorer",
        "probe", "mirror", "retractor", "plugger", "spreader",
        "condenser", "scissors", "plier", "pliers", "cutter",
        "clamp", "tweezer", "scaler", "curette", "periotome",
        "gauge", "caliper", "file", "files",
        "handpiece", "bur", "burs", "drill", "drills",
        "needle", "holder", "knife", "chisel",
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
    # Gutta-percha points are not paper/absorbent points (both are endo "points",
    # but obturation vs drying — "Sure Endo Gutta Percha #50" != "Sure Endo Paper
    # Points"). paper<->absorbent are synonyms, so they're on the same side.
    (frozenset({"gutta", "percha"}), frozenset({"paper", "absorbent"})),
    (frozenset({"conventional"}), frozenset({"mbt", "roth"})),
    (frozenset({"mbt"}), frozenset({"roth", "conventional", "duploslot"})),
    (frozenset({"roth"}), frozenset({"mbt", "conventional", "duploslot"})),
    (frozenset({"duploslot"}), frozenset({"standard", "mbt", "roth"})),
    (frozenset({"self-ligating"}), frozenset({"conventional"})),
    # Measurement tools vs the products they measure.
    # "Bracket positioning height gauge" is NOT the same as "bracket kit".
    (
        frozenset({"gauge", "gauges", "caliper", "ruler", "positioning"}),
        frozenset({"kit", "kits"}),
    ),
]

_WORD_RE = re.compile(r"\b[a-z0-9]+\b")

# Format/container nouns that are too generic to establish a match on their own.
# Two same-brand products that share ONLY these (plus the brand) but each carry
# their own distinctive token are different products — e.g. "Maarc Tray Adeziv"
# (a tray ADHESIVE) vs "Maarc Eazy Tray" (an impression tray). Shape/material
# words (diamond, straight, niti…) are deliberately NOT here — they discriminate.
_GENERIC_NOUNS: frozenset[str] = frozenset({
    "tray", "trays", "paper", "papers", "kit", "kits", "set", "sets",
    "box", "boxes", "pack", "packs", "refill", "refills", "bottle", "bottles",
    "pouch", "pouches", "roll", "rolls", "sheet", "sheets", "pad", "pads",
    "tube", "tubes", "jar", "jars", "syringe", "syringes",
})


def _no_shared_distinctive(search: str, found: str) -> bool:
    """True when both names carry a distinctive (non-brand, non-generic) token
    but share NONE — strong evidence they're different products of the same
    brand/category (e.g. 'adeziv'/'thinner' vs 'eazy'). Conservative: if either
    side is purely generic (no distinctive token of its own), do NOT fire — a
    terse competitor listing like 'Maarc Articulating Paper' should still match
    as a plausible base product."""
    sb, fb = extract_brand(search), extract_brand(found)
    s_dist = distinguishing_tokens(search) - {sb} - _GENERIC_NOUNS
    f_dist = distinguishing_tokens(found) - {fb} - _GENERIC_NOUNS
    if not s_dist or not f_dist:
        return False
    return not (s_dist & f_dist)


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


# Same-manufacturer brand aliases — NOT cross-brand matches. The left key is a
# house/sub-brand; the right phrases are the same company written differently.
# Kept tiny and explicit so brand discipline is preserved (e.g. "Avue" is the
# house line of "Dental Avenue"; pinkblue lists it as "Dental Avenue Avuecal").
_BRAND_ALIASES: dict[str, tuple[str, ...]] = {
    "avue": ("dental avenue",),
}


def _brand_match(a: Attributes, search: str, found: str) -> bool:
    if not a.brand:
        return True
    if _word_boundary(found, a.brand):
        return True
    for alias in _BRAND_ALIASES.get(a.brand, ()):
        if all(_word_boundary(found, w) for w in alias.split()):
            return True
    return False


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

    if _no_shared_distinctive(search, found):
        return GateResult(False, "no shared distinctive token")

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
