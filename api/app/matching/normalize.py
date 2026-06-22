"""
Pre-match text normalization. Port of lib/normalize.ts.

Product titles on different sites often append SKUs, pack counts, and
marketplace filler that make two identical products look different to
similarity metrics. Running both strings through normalize_for_match
before comparison eliminates those surface differences without discarding
the product-identity tokens a human reader would use.
"""
from __future__ import annotations

import re

_SKU_TAIL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\s*[-—–|]\s*(sku|mpn|code|item|ref|part)\s*[:#]?\s*[a-z0-9][a-z0-9\-_/]{2,}\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"\s*\((sku|mpn|code|item|ref|part)\s*[:#]?\s*[a-z0-9][a-z0-9\-_/]{2,}\)\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"\s*\[(sku|mpn|code|item|ref|part)\s*[:#]?\s*[a-z0-9][a-z0-9\-_/]{2,}\]\s*$",
        re.IGNORECASE,
    ),
    # NOTE: we deliberately do NOT strip a bare trailing "- ABC123" code. It is
    # far more often the VARIANT IDENTITY (e.g. "…Green HP - SDH101G" vs
    # "- SDH081G", "…Retractor - 079A") than marketplace noise — erasing it
    # collapses distinct sub-variants into one string and picks the wrong child.
    # The two-sided model-code gate tolerates a code present on one side only,
    # so keeping it doesn't hurt cross-site matching. Labeled SKU tails
    # ("- SKU: ABC123") above ARE stripped — those are genuine noise.
]

_PACK_TAIL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\s*[-—–|(]?\s*pack\s*of\s*\d+\s*\)?\s*$", re.IGNORECASE),
    re.compile(r"\s*[-—–|(]?\s*box\s*of\s*\d+\s*\)?\s*$", re.IGNORECASE),
    re.compile(r"\s*[-—–|(]?\s*set\s*of\s*\d+\s*\)?\s*$", re.IGNORECASE),
    re.compile(r"\s*[-—–|(]?\s*\d+\s*(pcs|pc|nos|units?)\s*\)?\s*$", re.IGNORECASE),
    re.compile(r"\s*[-—–|(]?\s*(moq|min\.?\s*order)\s*[:#]?\s*\d+\s*\)?\s*$", re.IGNORECASE),
]

_NOISE_TAIL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\s*[-—–|]\s*(buy\s+online|best\s+price|free\s+shipping|in\s+stock)\s*$",
        re.IGNORECASE,
    ),
    re.compile(r"\s*[-—–|]\s*dentalkart(\.com)?\s*$", re.IGNORECASE),
    re.compile(r"\s*[-—–|]\s*pinkblue(\.in)?\s*$", re.IGNORECASE),
]


# Telltale byte sequences left when UTF-8 text is wrongly decoded as
# CP1252/Latin-1 (the classic Excel/CSV "mojibake"): "Î¦"→Φ, "â€“"→–, "Âµ"→µ,
# "Â°"→°, "Ã©"→é, etc. Cheap gate before attempting a repair.
_MOJIBAKE_MARKERS = ("Ã", "Â", "Î", "â€", "Ð", "Ñ", " Å", " Â")


def fix_mojibake(text: str) -> str:
    """Recover UTF-8 text that was mis-decoded as CP1252/Latin-1.

    Uploaded sheets routinely arrive with "Labodent … - Î¦98*10" (should be Φ),
    "Retractor â€“ 50mm" (should be –), "40Âµ" (should be µ). Re-encoding to the
    wrong codec's bytes and decoding as UTF-8 restores the original. We only act
    when markers are present AND the round-trip yields valid UTF-8 — genuine text
    fails the decode and is returned untouched, so this is safe to run on
    everything. General: one repair covers Φ, –, µ, °, accented letters, … """
    if not text or text.isascii():
        return text
    if not any(m in text for m in _MOJIBAKE_MARKERS):
        return text
    try:
        repaired = text.encode("cp1252").decode("utf-8")
    except (UnicodeError, LookupError):
        return text
    return repaired if repaired != text else text


def _strip_with(patterns: list[re.Pattern[str]], name: str) -> str:
    out = name
    for pat in patterns:
        out = pat.sub("", out)
    return out.strip()


def strip_sku_tail(name: str) -> str:
    return _strip_with(_SKU_TAIL_PATTERNS, name)


def strip_pack_suffix(name: str) -> str:
    return _strip_with(_PACK_TAIL_PATTERNS, name)


def strip_noise_suffix(name: str) -> str:
    return _strip_with(_NOISE_TAIL_PATTERNS, name)


def normalize_for_match(name: str) -> str:
    cleaned = strip_noise_suffix(strip_pack_suffix(strip_sku_tail(fix_mojibake(name))))
    return re.sub(r"\s+", " ", cleaned).strip()
