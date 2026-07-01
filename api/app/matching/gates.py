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

# Generic dental INSTRUMENT / PRODUCT-TYPE words — every brand makes these, so a
# shared one is NOT evidence of the same brand. Used to stop the "competitor leads
# with the product-line word" exception from firing on a generic type: "Gracey
# Curette" shares "gracey" with a "Julldent … Gracey Curette" but is NOT a Julldent
# product. (Distinctive coined product lines like "Ketac"/"Fuji" are NOT in here, so
# those still pass.) Includes eponymous-but-now-generic designs (gracey, langer…).
_GENERIC_TYPES: frozenset[str] = frozenset({
    "curette", "curettes", "scaler", "scalers", "forceps", "probe", "probes",
    "explorer", "explorers", "elevator", "elevators", "mirror", "mirrors",
    "tweezer", "tweezers", "scissor", "scissors", "plier", "pliers", "chisel",
    "chisels", "condenser", "condensers", "burnisher", "burnishers", "spatula",
    "spatulas", "excavator", "excavators", "carver", "carvers", "applicator",
    "applicators", "sickle", "hoe", "file", "files", "reamer", "reamers",
    "spreader", "spreaders", "plugger", "pluggers", "retractor", "retractors",
    "separator", "separators", "scalpel", "needle", "needles", "blade", "blades",
    "cannula", "handpiece", "handpieces", "instrument", "instruments", "luxator",
    "periosteal", "osteotome", "raspatory", "gracey", "langer", "mccall",
    "columbia", "nabers", "barnhart", "morse", "goldman",
})

# Product-CATEGORY / material words — describe the KIND of product, never WHICH one,
# so they're not distinctive (a shared category is not a shared identity). Used to
# stop "glass ionomer restorative" shared between different lines (Ketac Molar vs
# Ketac Universal) from masking the real line difference.
_CATEGORY_WORDS: frozenset[str] = frozenset({
    "glass", "ionomer", "restorative", "restoration", "cement", "filling", "composite",
    "resin", "luting", "lining", "liner", "sealant", "varnish", "material", "dental",
    "gic", "rmgic", "compomer", "flowable", "packable", "microhybrid", "nanohybrid",
})

# CONTENT quantity — the actual amount in the pack (powder grams, liquid millilitres,
# …). A different content = a different product, even under the same brand + line
# (DK "15g Powder + 7.8mL Liquid" ≠ a "12.5 g Powder + 8.5 ml Liquid" pack). We read
# every weight/volume quantity and compare the DOMINANT (max) amount per unit; a
# relative gap beyond tolerance is a content mismatch. Dimension units (mm/cm/m) are
# deliberately excluded — those are sizes handled elsewhere, not pack content.
_CONTENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(gms|gm|grams|gram|kg|mg|ml|oz|g)\b", re.I)
_CONTENT_TOL = 0.12  # ≤12% apart = the same amount (7.8 vs 8.5 ml); 15 vs 12.5 g fails


def _content_quantities(text: str) -> dict[str, float]:
    """Max weight/volume amount per normalised unit found in the text."""
    out: dict[str, float] = {}
    for val, unit in _CONTENT_RE.findall(text.lower()):
        u = unit.lower()
        u = "g" if u in ("g", "gm", "gms", "gram", "grams") else u
        try:
            v = float(val)
        except ValueError:
            continue
        if v > 0:
            out[u] = max(out.get(u, 0.0), v)
    return out


def _content_mismatch(search: str, found: str) -> tuple[bool, str]:
    """True when both sides state a content amount in the SAME unit but the dominant
    amounts differ beyond tolerance (→ different pack / product). Fires only when the
    unit is present on BOTH sides (one-sided or absent content never gates)."""
    s = _content_quantities(search)
    f = _content_quantities(found)
    for unit in set(s) & set(f):
        sv, fv = s[unit], f[unit]
        hi = max(sv, fv) or 1.0
        if abs(sv - fv) / hi > _CONTENT_TOL:
            g = lambda x: f"{x:g}"  # noqa: E731
            return True, f"content mismatch: {g(sv)}{unit} vs {g(fv)}{unit}"
    return False, ""


def _shared_prefix(search: str, found: str) -> set[str]:
    """The leading run of identical words — the brand + product-line zone, which
    by convention sits at the START. Stripping it stops a multi-word brand's tail
    ('vm' in 'Tor Vm', 'espe' in '3M ESPE', 'sterilization' in 'Oro
    Sterilization') from masquerading as a shared *distinctive* token and
    defeating the gate below."""
    s, f = search.lower().split(), found.lower().split()
    i = 0
    while i < len(s) and i < len(f) and s[i] == f[i]:
        i += 1
    return set(s[:i])


def _no_shared_distinctive(search: str, found: str) -> bool:
    """True when both names carry a distinctive (non-brand, non-generic) token
    but share NONE — strong evidence they're different products of the same
    brand/category (e.g. 'adeziv'/'thinner' vs 'eazy', or 'proxicut' vs
    'polishing'). Conservative: if either side is purely generic (no distinctive
    token of its own), do NOT fire — a terse competitor listing like 'Maarc
    Articulating Paper' should still match as a plausible base product."""
    sb, fb = extract_brand(search), extract_brand(found)
    s_tok = distinguishing_tokens(search)
    f_tok = distinguishing_tokens(found)
    # Strip the shared leading run (the common brand + product-FAMILY zone). We used
    # to keep it when ≥3 words matched, assuming that meant "same product" — but that
    # masked the LINE word diverging right after: "3M ESPE Ketac **Molar**" vs "3M
    # ESPE Ketac **Universal**" share "3m espe ketac" (3 words) yet are different
    # products. Always strip the identical prefix; the category-word drop below keeps
    # legitimate descriptor-only differences ("… Restorative" vs "… Filling") matching.
    prefix = _shared_prefix(search, found)
    # Only DROP a generic noun (pouch/box/kit/tray…) when BOTH names share it —
    # then it's incidental packaging ("Maarc Eazy Tray" vs "Maarc Tray Adeziv",
    # both trays). A generic noun that DIFFERS between the two ("Reel" vs "Pouch")
    # IS the product's identity and must stay as a distinctive token.
    shared_generic = s_tok & f_tok & _GENERIC_NOUNS
    # Product-CATEGORY / material words describe what KIND of product it is, not WHICH
    # one — never distinctive, so drop from both sides. Without this, "glass ionomer
    # restorative" shared between "Ketac Molar" and "Ketac Universal" would look like a
    # shared identity and hide that molar≠universal.
    drop = {sb, fb} | prefix | shared_generic | _CATEGORY_WORDS
    # Stem so plurals match (reels == reel, discs == disc).
    s_dist = {_stem(w) for w in s_tok - drop}
    f_dist = {_stem(w) for w in f_tok - drop}
    # If the INPUT has no distinctive token of its own, we can't discriminate — a
    # terse listing is a plausible base, so don't fire.
    if not s_dist:
        return False
    # The input HAS distinctive words. If the candidate shares NONE of them it's a
    # different product — including when the candidate is all brand+generic+
    # stopword ("Oro Dental Kit" for an "Oro Sterilization Reel": no shared
    # sterilization/reel). A real terse base would still share the key word.
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
    # Manufacturer ⇄ product-line equivalences that no rule can infer (knowledge,
    # like a search engine's knowledge graph). Kids-e-Crown is Shinhung's line, so
    # a competitor listing the same item under the maker "Shinhung" still matches.
    "kidsecrown": ("shinhung",),
}


# Words that introduce a COMPATIBILITY reference rather than the product's own
# brand. "Dental Apex Locator Cable For E2ZZ, J-Morita" is a third-party cable
# that FITS J-Morita — not a J-Morita product. ("by"/"from" = made-by, NOT here.)
_COMPAT_MARKERS = frozenset({
    "for", "fits", "fit", "compatible", "compatibles", "suitable",
    "replacement", "spare", "compatibility",
})


def _brand_compat_only(found: str, brand: str) -> bool:
    """True when the brand appears in `found` ONLY as a compatibility reference —
    i.e. NOT in the first few words (where a real brand lives) AND introduced by a
    compatibility marker ('… For E2ZZ, J-Morita'). Brands belong at the start; a
    brand mentioned only after 'For/Fits/Compatible' names what the item FITS."""
    bwords = brand.split()
    words = re.sub(r"[^a-z0-9 ]", " ", found.lower()).split()

    def _locate() -> int | None:
        # Exact word-run match first.
        for i in range(len(words)):
            if words[i:i + len(bwords)] == bwords:
                return i
        # Single-word brand fused onto a 1–2 letter INITIAL ("J-Morita" → "jmorita"
        # after normalization): match the token whose tail IS the brand. Without
        # this, a third-party "… Cable For E2ZZ, J-Morita" hides the brand and the
        # fitment guard misses it.
        if len(bwords) == 1:
            b = bwords[0]
            for i, w in enumerate(words):
                if w != b and w.endswith(b) and 1 <= len(w) - len(b) <= 2 and w[:-len(b)].isalpha():
                    return i
        return None

    first = _locate()
    if first is None or first <= 2:   # brand at/near the start = the real brand
        return False
    return any(w in _COMPAT_MARKERS for w in words[:first])


def _brand_match(a: Attributes, search: str, found: str, found_desc: str = "") -> bool:
    if not a.brand:
        return True
    # A brand that shows up only as "… For <brand>" is a compatibility note, not
    # the product's brand — reject before the lenient containment checks below.
    if _brand_compat_only(found, a.brand):
        return False
    if _word_boundary(found, a.brand):
        return True
    for alias in _BRAND_ALIASES.get(a.brand, ()):
        if all(_word_boundary(found, w) for w in alias.split()):
            return True
    # House line spelled as a single coined word: brand "Avue" → product line
    # "AvueCal" (oralkart "AvueCal - Calcium Hydroxide…"). Only for brands long
    # enough (≥4) that a prefix is meaningful, to avoid "pro"→"product" noise.
    if len(a.brand) >= 4:
        for w in _WORD_RE.findall(found.lower()):
            if w != a.brand and w.startswith(a.brand):
                return True
    # Spacing / hyphenation variant of the SAME brand: "Oracraft" == "Ora Craft"
    # == "Ora-Craft" (pinkblue spells Ora Craft, DK spells Oracraft). Compare
    # with all non-alphanumerics stripped. ≥4 chars so it stays brand-specific
    # ("GDC" stays ≠ "Oracraft").
    if len(a.brand) >= 4 and a.brand in re.sub(r"[^a-z0-9]", "", found.lower()):
        return True
    # Competitor DROPPED the manufacturer and leads with the PRODUCT LINE — "Ketac
    # Molar" for DK's "3M ESPE Ketac Molar", "Fuji IX" for "GC Fuji IX". If the
    # found name's own leading brand-token is a distinctive word that appears in the
    # SEARCH name, it's the same product line → accept despite the missing brand.
    # (Mirrors the containment check in structured._brand_conflict.) A genuinely
    # different brand ("GDC …") whose token is NOT in the search still fails.
    fb = extract_brand(found)
    if (fb and len(fb) >= 4 and fb != a.brand
            and fb not in _GENERIC_NOUNS and fb not in _GENERIC_TYPES
            and _word_boundary(search, fb)):
        return True
    # The competitor's TITLE may omit the brand while its DESCRIPTION states it —
    # "Ketac Molar" (title) / "Ketac Molar by 3M ESPE …" (description). Check the
    # brand in the FIRST part of the description (where a product states its own
    # brand), guarded against "compatible with <other brand>" notes.
    if found_desc:
        head = found_desc[:240].lower()
        if not _brand_compat_only(head, a.brand) and _word_boundary(head, a.brand):
            return True
        for alias in _BRAND_ALIASES.get(a.brand, ()):
            if all(_word_boundary(head, w) for w in alias.split()):
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


# ── Contrast dimensions ────────────────────────────────────────────────────
# Each set is a VARIANT AXIS: products that take DIFFERENT values on the same
# axis are different products — even when their names are 90%+ similar and the
# differing words look alike ("intraoral" vs "extraoral" are 78% identical
# characters, so similarity scoring reads them as near-matching). This is the
# "look at the DIFFERENCE, not the similarity" layer: it gives the one decisive
# word veto power over a pile of shared filler (Penta/Ortho/size/Oz). Each axis
# below is variant-defining in dental products — taking two different values from
# the same axis is never the same SKU. (Numeric axes — size 5/8 vs 3/8, 3.5oz vs
# 8oz, microns, ISO — are handled as model-code discriminators in attributes.)
_CONTRAST_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"intraoral", "extraoral"}),
    frozenset({"upper", "lower"}),
    frozenset({"left", "right"}),
    frozenset({"anterior", "posterior"}),
    frozenset({"mesial", "distal"}),
    frozenset({"buccal", "lingual", "palatal"}),
    frozenset({"maxillary", "mandibular"}),
    frozenset({"internal", "external"}),
    frozenset({"male", "female"}),
    frozenset({"straight", "curved", "angled"}),
    frozenset({"small", "medium", "large"}),
    frozenset({"fine", "coarse"}),                 # grit (medium omitted: shared w/ size)
    frozenset({"short", "long"}),
    frozenset({"single", "double"}),
    frozenset({"pediatric", "paediatric", "adult"}),
    frozenset({"primary", "permanent"}),
    frozenset({"horizontal", "vertical"}),
    # Dental-cement FUNCTION — a restorative/filling cement (e.g. Ketac Molar) is
    # a different product from a luting cement that cements crowns (Ketac Cem),
    # even though both are "glass ionomer cement". (synonyms not mixed in.)
    frozenset({"restorative", "luting"}),
    # TOOTH POSITION — pediatric crowns / refills come per tooth: a "Canine" refill
    # is a different product from a "Central" or "Molar" one (Kids-e-Crown). Only
    # distinct positions listed (no synonyms like cuspid/incisor, to avoid
    # same-tooth false rejects); fires only when both names name a DIFFERENT tooth.
    frozenset({"central", "lateral", "canine", "premolar", "molar"}),
)


def _contrast_mismatch(s_words: set[str], f_words: set[str]) -> str | None:
    """Return a reason when the two names take DIFFERENT values on the same
    variant axis (e.g. one 'intraoral', the other 'extraoral'). Fires only when
    each side carries a value from the axis and they share none — so a name that
    mentions both, or neither, never trips."""
    for group in _CONTRAST_GROUPS:
        s_has = group & s_words
        f_has = group & f_words
        if s_has and f_has and not (s_has & f_has):
            return f"{'/'.join(sorted(s_has))} vs {'/'.join(sorted(f_has))}"
    return None


# ── General numeric / serial signature ─────────────────────────────────────
# Pull EVERY meaningful number and code from a name, so ANY differing size/serial
# splits two otherwise-similar products — no bespoke regex per format. This is the
# general rule for "the numbers/serials must match". Pack/quantity counts are
# stripped (handled by pack + per-unit price), and a number's trailing unit is
# dropped so "15g" == "15", "3.5 inch" == "3.5".
_PACK_CTX_RE = re.compile(
    r"\b(?:pack|box|set|pair|jar|packet|pouch|kit|strip|card|bundle|lot|case)s?\s*"
    r"(?:of\s*)?\d+"
    r"|\b\d+\s*(?:pcs?|nos?|units?|pieces?|tablets?|caps?|capsules?|sheets?|"
    r"sachets?|strips?|tips?|rolls?|ml|l|cc)\b",
    re.IGNORECASE,
)
# A serial/code = 2+ letters glued to digits (FX51P, EXS6). A number = integer /
# decimal / fraction / dashed serial, kept WHOLE so a variant suffix stays with
# it: "1.099-1" ≠ "1.099-2", "1.732" ≠ "1.733". Only the leading digits' trailing
# unit is dropped (15g == 15).
_NUM_SIG_RE = re.compile(
    r"[a-z]{2,}\d[a-z0-9]*|\d+(?:\.\d+)?(?:[-/]\d+)?", re.IGNORECASE
)


def _spec_numbers(name: str) -> set[str]:
    cleaned = _PACK_CTX_RE.sub(" ", name.lower())
    return {m.group(0) for m in _NUM_SIG_RE.finditer(cleaned)}


def _number_conflict(search: str, found: str) -> str | None:
    """Both names carry numbers/serials but share NONE → different size/variant.
    One-sided numbers (a SKU only the competitor lists) never fire — only a true
    conflict does."""
    s = _spec_numbers(search)
    f = _spec_numbers(found)
    if s and f and not (s & f):
        return f"{'/'.join(sorted(s)[:3])} vs {'/'.join(sorted(f)[:3])}"
    return None


def gate_check(search: str, found: str, found_desc: str = "") -> GateResult:
    s_attrs = extract_attributes(search)
    f_attrs = extract_attributes(found)

    if not _brand_match(s_attrs, search, found, found_desc):
        return GateResult(False, f"brand mismatch: '{s_attrs.brand}' not in '{found}'")

    s_words = _words(search)
    f_words = _words(found)

    if _incompatible_types(s_words, f_words):
        return GateResult(False, "incompatible product types")

    if _category_exclusion(s_words, f_words):
        return GateResult(False, "category exclusion")

    _contrast = _contrast_mismatch(s_words, f_words)
    if _contrast:
        return GateResult(False, f"variant mismatch: {_contrast}")

    if _no_shared_distinctive(search, found):
        return GateResult(False, "no shared distinctive token")

    if s_attrs.iso_size and f_attrs.iso_size and s_attrs.iso_size != f_attrs.iso_size:
        return GateResult(False, f"iso size mismatch: {s_attrs.iso_size} vs {f_attrs.iso_size}")

    if s_attrs.shade and f_attrs.shade and s_attrs.shade != f_attrs.shade:
        return GateResult(False, f"shade mismatch: {s_attrs.shade} vs {f_attrs.shade}")

    # Content amount (powder g / liquid ml …). The competitor's amount often lives in
    # its DESCRIPTION, not its title, so include found_desc.
    bad_qty, qty_reason = _content_mismatch(search, found + " " + found_desc)
    if bad_qty:
        return GateResult(False, qty_reason)

    # Colour variant: same item in a different colour is a different product
    # ("Kalabhai Ultra Rock Die (Brown)" ≠ "(Yellow)"). Fire only when BOTH names
    # carry colours that are DISJOINT — sharing one (e.g. "Blue & Red" vs "Blue")
    # is not a mismatch, and a one-sided colour never gates.
    if s_attrs.colors and f_attrs.colors and not (s_attrs.colors & f_attrs.colors):
        return GateResult(
            False,
            f"colour mismatch: {'/'.join(sorted(s_attrs.colors))} vs {'/'.join(sorted(f_attrs.colors))}",
        )

    if (
        s_attrs.concentration is not None
        and f_attrs.concentration is not None
        and abs(s_attrs.concentration - f_attrs.concentration) > 1e-6
    ):
        return GateResult(False, "concentration mismatch")

    if s_attrs.taper and f_attrs.taper and s_attrs.taper != f_attrs.taper:
        return GateResult(False, "taper mismatch")

    # Hand-instrument tip/size number ("…-1 EXC32L" vs "…- 6 EXC32L", "#3" vs
    # "#6") — same code, different tip = a different physical instrument.
    if s_attrs.tip_number and f_attrs.tip_number and s_attrs.tip_number != f_attrs.tip_number:
        return GateResult(False, f"tip number mismatch: {s_attrs.tip_number} vs {f_attrs.tip_number}")

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

    # General catch-all: both names carry numbers/serials that share none → a
    # different size/variant (3.5 vs 4.5, 016x022 vs 017x025, FX51P vs FX67AS).
    # Runs last so the specific gates above give better reasons first.
    _nc = _number_conflict(search, found)
    if _nc:
        return GateResult(False, f"number/serial conflict: {_nc}")

    return GateResult(True)
