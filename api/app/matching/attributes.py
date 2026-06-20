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


_MODEL_RE = re.compile(r"\b([a-z]{1,5}-?\d{2,5}[a-z]?)\b", re.IGNORECASE)
# USP suture gauge, e.g. "#2-0", "2-0", "5/0" — a hard size discriminator
# (Meril Filasilk #2-0 ≠ #5-0). Normalized to "<n>-0".
_SUTURE_RE = re.compile(r"#?\b(\d{1,2})\s*[-/]\s*0\b")
# Integer dimension pair, e.g. archwire "17 x 25" (the decimal form ".017 x .025"
# is handled by _DIM_PAIR_RE). Normalized to "<a>x<b>".
_DIM_INT_RE = re.compile(r"\b(\d{2})\s*[x×*]\s*(\d{2})\b", re.IGNORECASE)  # noqa: RUF001
# Articulating-paper / shim-stock thickness in microns, e.g. "70 Microns",
# "40µ Microns", "100µ" — a hard variant discriminator (40µ ≠ 70µ). The bare
# "u" unit is excluded (too ambiguous). Normalized to "<n>u".
_MICRON_RE = re.compile(r"\b(\d{2,3})\s*(?:µ|μ|microns?)", re.IGNORECASE)  # noqa: RUF001
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


def extract_brand(name: str) -> str:
    """Brand = first alphanumeric chunk before any hyphen/space.

    "LM-SlimLift"   -> "lm"
    "3M Filtek"     -> "3m"
    "OrthoMetric X" -> "orthometric"
    """
    parts = name.strip().split()
    if not parts:
        return ""
    m = _BRAND_PREFIX_RE.match(parts[0])
    return m.group(0).lower() if m else parts[0].lower()


def _first_match(pat: re.Pattern[str], text: str) -> str | None:
    m = pat.search(text)
    return m.group(1) if m else None


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
    model_codes += [f"{m.group(1)}-0" for m in _SUTURE_RE.finditer(name)]
    model_codes += [f"{m.group(1)}x{m.group(2)}" for m in _DIM_INT_RE.finditer(name)]
    model_codes += [f"{m.group(1)}u" for m in _MICRON_RE.finditer(name)]

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
