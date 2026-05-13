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


_MODEL_RE = re.compile(r"\b([a-z]{1,5}-?\d{2,5}[a-z]?)\b", re.IGNORECASE)
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


def extract_brand(name: str) -> str:
    parts = name.strip().split()
    if not parts:
        return ""
    return parts[0].lower()


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

    viscosity: str | None = None
    for v in _VISCOSITY_VARIANTS:
        if v in lower:
            viscosity = v
            break

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
    )
