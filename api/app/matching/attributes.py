"""
Attribute extraction. Port of the regex-based feature extractors that live
inside lib/smart-matcher.ts and lib/variant-extractor.ts. Pure functions —
no I/O. Returned as a dataclass for clean downstream comparison.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(slots=True)
class Attributes:
    brand: str = ""
    model_codes: list[str] = field(default_factory=list)
    iso_size: int | None = None
    shade: str | None = None
    concentration: float | None = None
    taper: str | None = None
    slot: str | None = None
    pack_count: int | None = None
    viscosity: str | None = None
    material: str | None = None
    dimension: str | None = None
    wire_form: str | None = None
    tip_number: int | None = None
    colors: frozenset[str] = frozenset()


_MODEL_RE = re.compile(r"\b([a-z]{1,5}-?\d{2,5}[a-z]?)\b", re.IGNORECASE)
# Alpha-prefixed SKU/catalog tail code that _MODEL_RE misses because it has a
# SINGLE digit, e.g. "EXS6", "POW6", "EXD5" (Oracraft probes), "PCP11". Requiring
# 3+ CONTIGUOUS letters then digit(s) keeps it SKU-like and excludes sizes
# ("15g" is digit-first), shades ("A3" is one letter) and spaced numbers
# ("No 6", "Pack of 5"). Distinct codes here are a hard discriminator: the WHO
# probe "PCP11.5B" and the plain probe "EXS6" are different instruments that
# otherwise read as near-duplicates ("…Single Ended Probe #3"). A small word
# guard drops common word+digit tokens that aren't codes.
_SKU_RE = re.compile(r"\b([a-z]{3,5})(\d{1,4}[a-z]?)\b", re.IGNORECASE)
_SKU_STOP = {"type", "size", "pack", "pair", "step", "size", "grit", "part"}

# Colour variant words. Dental products (die stones, gypsum, denture bases,
# elastics, articulating papers, retraction caps…) commonly ship the same item in
# several colours that are DIFFERENT products: "Kalabhai Ultra Rock Die (Brown)"
# ≠ "(Yellow)". When both names carry colours that are DISJOINT (no colour in
# common) it's a different variant. Excludes "gold"/"silver"/"natural"/"ivory"
# (usually a product-line or material descriptor, e.g. "GC Gold Label").
_COLOR_WORDS = frozenset({
    "brown", "yellow", "green", "blue", "red", "pink", "orange", "purple",
    "violet", "white", "black", "grey", "gray", "maroon",
})
# Small instrument tip/size designator — "#6", "No. 3", "Excavator -1", "- 6".
# Distinguishes hand-instrument tips that share the SAME model code
# (GDC "…-1 EXC32L" vs "…- 6 EXC32L"). A hyphen designator must be SPACE-
# separated so a code hyphen ("TR-13", "856-018M", "DL-300", "2-0") is NOT
# mistaken for a tip. A units/pack/number lookahead keeps "- 40mm", "Pack of 5",
# "016 X 022" out. 1–2 digits only (tips are small).
# Designator forms recognized (all mean the same tip): "#6", "No. 3", "- 6",
# "-1", and a BARE word-trailing number ("Excavator 3"). So "#3" and "3" are the
# SAME tip. A hyphen designator must be space-separated (so "TR-13"/"DL-300"/
# "2-0" code hyphens don't count); the bare form must follow a letter+space and
# NOT "of " (so "Pack of 3" is excluded). The lookahead drops units/measure/
# multi-digit/code tails.
_TIP_RE = re.compile(
    r"(?:#\s*|\bno\.?\s*|(?:^|\s)[-–]\s*|(?<=[a-zA-Z]\s)(?<!of\s))"  # noqa: RUF001
    r"(\d{1,2})"
    r"(?![\d./\-])"  # not part of the SAME number/code ("6.5", "6/0", "2-0", "63")
    r"(?!\s*(?:mm|cm|ml|gm?|kg|oz|micron|µ|%|pcs?|nos?|units?|sets?|sheets?|ply|kit|burs?|x\b))",  # noqa: RUF001
    re.IGNORECASE,
)
# USP suture gauge, e.g. "#2-0", "2-0", "5/0" — a hard size discriminator
# (Meril Filasilk #2-0 ≠ #5-0). Normalized to "<n>-0".
_SUTURE_RE = re.compile(r"#?\b(\d{1,2})\s*[-/]\s*0\b")
# Integer dimension pair, e.g. archwire "17 x 25" or the 3-digit leading-zero
# form "016 X 022" (the decimal ".017 x .025" is handled by _DIM_PAIR_RE). Both
# normalize to "<a>x<b>" so "016x022" ≠ "017x025" is a hard size discriminator.
_DIM_INT_RE = re.compile(r"\b(\d{2,3})\s*[x×*]\s*(\d{2,3})\b", re.IGNORECASE)  # noqa: RUF001
# Articulating-paper / shim-stock thickness in microns, e.g. "70 Microns",
# "40µ Microns", "100µ" — a hard variant discriminator (40µ ≠ 70µ). The bare
# "u" unit is excluded (too ambiguous). Normalized to "<n>u".
_MICRON_RE = re.compile(r"\b(\d{2,3})\s*(?:µ|μ|microns?)", re.IGNORECASE)  # noqa: RUF001
# Force / volume in ounces — ortho elastics "3.5 Oz", "8 Oz", "2 oz" (the elastic
# force), or fluid volume. A hard size discriminator (3.5oz ≠ 8oz). Normalized
# "<n>oz".
_OZ_RE = re.compile(r"\b(\d{1,2}(?:\.\d)?)\s*oz\b", re.IGNORECASE)
# Fraction-inch size — ortho elastic diameter / instrument fractions: 1/8, 3/8,
# 5/16, 1/2, 5/8 (5/8 ≠ 3/8). Denominator restricted to real inch fractions
# (2/4/8/16/32) so ratios ("1:1"), suture "/0" and dates don't match. Normalized
# "<n>/<d>".
_FRAC_RE = re.compile(r"\b([1-9]\d?)/(2|4|8|16|32)\b")
# Standalone decimal SIZE — articulator / instrument inches: "3.5", "4.5" (the
# trailing " inch-mark is stripped by normalize), "11.5 inch". A hard size
# discriminator (3.5 ≠ 4.5). Excludes decimals glued to a code (letter/dot
# before), 2-decimal values ("5.25"), and unit-bearing weight/volume handled
# elsewhere (oz/ml/g/mg/kg). Normalized "sz<n>".
_DECIMAL_SIZE_RE = re.compile(
    r"(?<![a-z0-9.])(\d{1,2}\.\d)(?![\d.])(?!\s*(?:oz|ml|mg|mcg|kg|gm?\b))",
    re.IGNORECASE,
)
_ISO_RE = re.compile(r"(?:#|no\.|size|iso)\s*(\d{2,3})\b", re.IGNORECASE)
_SHADE_RE = re.compile(r"\b([A-D][1-4](?:\.5)?|BW|UD)\b")
_CONC_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_TAPER_RE = re.compile(r"\.?(0[2-9])\b")
_SLOT_RE = re.compile(r"\b0?\.?(018|020|022)\b")
_PACK_RE = re.compile(
    r"\b(?:pack\s*of|box\s*of|set\s*of|x)\s*(\d+)\b|\b(\d+)\s*(?:pcs|pc|nos|units?)\b",
    re.IGNORECASE,
)
_VISCOSITY_VARIANTS = ("light body", "heavy body", "putty", "wash", "monophase")
# (?<!\d) instead of \b: a leading bare dot (".017") has no word boundary
# after a space, so \b would never match the dotted form.
# The multiplication sign is a deliberate alternative separator.
_DIM_PAIR_RE = re.compile(r"(?<!\d)0?\.(\d{3})\s*[x×*]\s*0?\.(\d{3})\b")  # noqa: RUF001
_WIRE_FORM_RE = re.compile(r"\b(upper|lower)\b", re.IGNORECASE)
# Longest-first so "nickel titanium" wins over "titanium".
_MATERIALS: list[tuple[str, str]] = [
    ("nickel titanium", "niti"), ("niti", "niti"),
    ("stainless steel", "stainless steel"),
    ("tungsten carbide", "tungsten carbide"),
    ("titanium", "titanium"), ("ceramic", "ceramic"), ("zirconia", "zirconia"),
]


_BRAND_PREFIX_RE = re.compile(r"^[a-z0-9]+", re.IGNORECASE)


# Words that, following a lone leading letter, mean the letter is an ATTRIBUTE
# (size / grade / shape / film-speed) rather than a brand INITIAL — so the letter
# must be kept, not folded away. "S Cartridge"/"M Brush" (sizes), "D Speed"/"E
# Speed" (X-ray film speeds), "L Curve" (shape) are different products, NOT the
# same brand. A genuine brand initial is followed by a proper brand NOUN ("J
# Morita", "B Braun"), never by one of these generic/dimension words. Keeping this
# a blocklist (not the inverse) is deliberate: brands are open-ended, but the set
# of generic head-nouns/sizes that follow an attribute letter is small and stable.
_NOT_A_BRAND_WORD: frozenset[str] = frozenset({
    # generic packaging / form nouns
    "tray", "trays", "paper", "papers", "kit", "kits", "set", "sets",
    "box", "boxes", "pack", "packs", "refill", "refills", "bottle", "bottles",
    "pouch", "pouches", "roll", "rolls", "sheet", "sheets", "pad", "pads",
    "tube", "tubes", "jar", "jars", "syringe", "syringes",
    # generic product head-nouns
    "cartridge", "cartridges", "brush", "brushes", "film", "films", "file",
    "files", "cable", "cables", "tip", "tips", "burs", "bur", "needle",
    "needles", "forceps", "scaler", "wire", "wires", "band", "bands",
    # dimension / grade / shape descriptors
    "speed", "size", "sizes", "type", "shape", "curve", "small", "medium",
    "large", "fine", "coarse", "short", "long", "round", "square",
})


def extract_brand(name: str) -> str:
    """Brand = first alphanumeric chunk before any hyphen/space.

    "LM-SlimLift"   -> "lm"
    "3M Filtek"     -> "3m"
    "OrthoMetric X" -> "orthometric"
    "J Morita ZX …" -> "morita"   (a single-letter initial is optional, see below)
    """
    parts = name.strip().split()
    if not parts:
        return ""
    first = parts[0]
    # A coined, MULTI-segment hyphenated first token is one brand/line as a whole
    # ("Kids-e-Crown" → "kidsecrown", NOT "kids"; "e-Max-Press"). Shredding it at
    # the first hyphen leaves a meaningless fragment that then rejects the real
    # product. ≥2 internal hyphens marks a coined name; single-hyphen brand-line
    # tokens ("LM-SlimLift" → "lm", "Ora-Craft" → "ora") keep first-chunk behaviour.
    if first.count("-") >= 2 and re.search(r"[a-zA-Z]", first):
        joined = re.sub(r"[^a-z0-9]", "", first.lower())
        if len(joined) >= 4:
            return joined
    m = _BRAND_PREFIX_RE.match(first)
    brand = m.group(0).lower() if m else first.lower()
    # A lone single LETTER as the first token is usually a brand INITIAL, not the
    # brand: "J Morita" (J. Morita Corp), "B Braun". Competitors routinely drop the
    # initial ("Morita ZX Apex Locator…" for DK's "J Morita ZX Apex Locator…"), so
    # anchoring the brand to "j" rejects the IDENTICAL product. Fold the initial
    # into the next word — BUT only when that next word is a real brand noun, not a
    # size/grade/generic head-noun ("S Cartridge", "D Speed", "M Brush"), where the
    # letter is the distinguishing attribute and MUST be kept. Digits ("2-0" → "2",
    # "3M") are never single-letter alphabetic, so they're untouched.
    if len(brand) == 1 and brand.isalpha() and len(parts) >= 2:
        m2 = _BRAND_PREFIX_RE.match(parts[1])
        nxt = (m2.group(0).lower() if m2 else parts[1].lower())
        if nxt.isalpha() and len(nxt) >= 3 and nxt not in _NOT_A_BRAND_WORD:
            return nxt
    return brand


def _first_match(pat: re.Pattern[str], text: str) -> str | None:
    m = pat.search(text)
    return m.group(1) if m else None


# A standalone single UPPERCASE letter is a MODEL DESIGNATOR — "UDS E" vs "UDS P",
# "Type A" vs "Type B", "D Speed" vs "E Speed" — i.e. a DIFFERENT product. Captured
# as a model code (namespaced 'ml_') so the model-code gate separates differing
# letters (E ≠ P) yet still tolerates one side omitting it. Only uppercase (real
# designators are capitalised; incidental lowercase letters aren't). Articles A/I
# and the dimension marker X are excluded.
_MODEL_LETTER_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z](?![A-Za-z0-9])")
_MODEL_LETTER_STOP = frozenset({"A", "I", "X"})


def extract_attributes(name: str) -> Attributes:
    lower = name.lower()

    iso_match = _first_match(_ISO_RE, name)
    shade_match = _first_match(_SHADE_RE, name)
    conc_match = _first_match(_CONC_RE, name)
    taper_match = _first_match(_TAPER_RE, name)
    slot_match = _first_match(_SLOT_RE, name)

    pack_count: int | None = None
    pm = _PACK_RE.search(name)
    if pm:
        pack_count = int(pm.group(1) or pm.group(2))

    model_codes = [m.group(1).lower() for m in _MODEL_RE.finditer(name)]
    model_codes += [
        (m.group(1) + m.group(2)).lower()
        for m in _SKU_RE.finditer(name)
        if m.group(1).lower() not in _SKU_STOP
    ]
    model_codes += [f"{m.group(1)}-0" for m in _SUTURE_RE.finditer(name)]
    model_codes += [f"{m.group(1)}x{m.group(2)}" for m in _DIM_INT_RE.finditer(name)]
    model_codes += [f"{m.group(1)}u" for m in _MICRON_RE.finditer(name)]
    model_codes += [f"{m.group(1)}oz" for m in _OZ_RE.finditer(name)]
    model_codes += [f"{m.group(1)}/{m.group(2)}" for m in _FRAC_RE.finditer(name)]
    model_codes += [f"sz{m.group(1)}" for m in _DECIMAL_SIZE_RE.finditer(name)]
    model_codes += [
        f"ml_{m.group(0).lower()}"
        for m in _MODEL_LETTER_RE.finditer(name)
        if m.group(0) not in _MODEL_LETTER_STOP
    ]

    viscosity: str | None = None
    for v in _VISCOSITY_VARIANTS:
        if v in lower:
            viscosity = v
            break

    material: str | None = None
    for needle, canon in _MATERIALS:
        if needle in lower:
            material = canon
            break

    dim = _DIM_PAIR_RE.search(lower)
    dimension = f"{dim.group(1)}x{dim.group(2)}" if dim else None

    wf = _WIRE_FORM_RE.search(lower)
    wire_form = wf.group(1).lower() if wf else None

    colors = frozenset(re.findall(r"[a-z]+", lower)) & _COLOR_WORDS

    tip_m = _TIP_RE.search(name)
    tip_number = int(tip_m.group(1)) if tip_m else None
    # A number that is actually the pack count ("Pack of 5" → 5) is not a tip.
    if tip_number is not None and tip_number == pack_count:
        tip_number = None

    return Attributes(
        brand=extract_brand(name),
        model_codes=model_codes,
        iso_size=int(iso_match) if iso_match else None,
        shade=shade_match.lower() if shade_match else None,
        concentration=float(conc_match) if conc_match else None,
        taper=taper_match,
        slot=slot_match,
        pack_count=pack_count,
        viscosity=viscosity,
        material=material,
        dimension=dimension,
        wire_form=wire_form,
        tip_number=tip_number,
        colors=colors,
    )


# Variant attributes that may be recovered from description/packaging when
# the name lacks them. Filled ONLY when the extra text yields exactly one
# distinct value — descriptions often enumerate every available variant
# ("shades A1, A2, A3"), and guessing one of those would corrupt matching.
_RICH_FIELDS: tuple[str, ...] = (
    "iso_size", "shade", "concentration", "viscosity",
    "material", "dimension", "wire_form", "pack_count",
)

_FINDALL_RES: dict[str, re.Pattern[str]] = {
    "shade": _SHADE_RE,
    "concentration": _CONC_RE,
    "iso_size": _ISO_RE,
}


def _unambiguous(field: str, text: str) -> str | None:
    """Return the single distinct value of `field` in `text`, else None."""
    pat = _FINDALL_RES.get(field)
    if pat is not None:
        values = {m.lower() for m in pat.findall(text) if m}
        return values.pop() if len(values) == 1 else None
    return None


def extract_attributes_rich(
    name: str, description: str = "", packaging: str = ""
) -> Attributes:
    """Attributes from the name, with gaps filled from description+packaging.

    Name always wins. Extra text only fills a missing field when it contains
    exactly one distinct value for it (see _RICH_FIELDS note).
    """
    attrs = extract_attributes(name)
    extra = f"{description} {packaging}".strip()
    if not extra:
        return attrs
    extra_attrs = extract_attributes(extra)
    extra_lower = extra.lower()

    for field_name in _RICH_FIELDS:
        if getattr(attrs, field_name) is not None:
            continue
        unamb = _unambiguous(field_name, extra)
        if unamb is not None:
            if field_name == "iso_size":
                setattr(attrs, field_name, int(unamb))
            elif field_name == "concentration":
                setattr(attrs, field_name, float(unamb))
            else:
                setattr(attrs, field_name, unamb)
            continue
        if field_name in ("material", "dimension", "wire_form", "viscosity", "pack_count"):
            # These extractors already return one value; ambiguity is rare
            # ("upper" AND "lower" in one description is the exception).
            if field_name == "wire_form" and len(set(_WIRE_FORM_RE.findall(extra_lower))) > 1:
                continue
            setattr(attrs, field_name, getattr(extra_attrs, field_name))
    return attrs
