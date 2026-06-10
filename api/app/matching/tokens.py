"""
Tokenization and lightweight similarity features.

These supplement the sentence-transformer cosine in `score.py`:
  * `weighted_overlap`  — Jaccard-like overlap that downweights common
    dental stopwords (so "Wizdent" matters more than "dental").
  * `fuzz_ratio`        — rapidfuzz token_set_ratio for fast string
    overlap that's robust to word reordering.

Data structures:
  * `_STOPWORDS`        — frozenset for O(1) common-word lookup.
  * `_TOKEN_RE`         — compiled once at import.
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Common dental/marketing tokens that shouldn't decide a match. Anything in
# this set still contributes to overlap, but at 0.1 weight instead of 1.0.
_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "in", "is", "of", "on", "or", "the", "to", "with",
    "dental", "dentist", "medical", "surgical", "supplies", "equipment",
    "devices", "product", "products", "buy", "online", "best", "price",
    "free", "delivery", "shipping", "india", "new", "original", "genuine",
    "type", "shape", "style", "model", "series", "version",
    "ss", "stainless", "steel", "pack", "set", "box", "kit", "kits",
    "premium", "professional", "standard", "regular", "extra",
    "small", "medium", "large", "xl", "xxl", "light", "heavy",
    "red", "blue", "green", "yellow", "white", "black", "pink", "clear",
})

_STOPWORD_WEIGHT = 0.1
_DEFAULT_WEIGHT = 1.0


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def distinguishing_tokens(text: str) -> set[str]:
    """Tokens with the noise stripped — useful for sparse pre-filter."""
    return {t for t in tokenize(text) if t not in _STOPWORDS}


def _weight(token: str) -> float:
    return _STOPWORD_WEIGHT if token in _STOPWORDS else _DEFAULT_WEIGHT


def weighted_overlap(search: str, candidate: str) -> float:
    """Weighted Jaccard between the two token sets.

    Stopword tokens contribute a fraction of a regular token's weight, so two
    products that only share "dental" don't get credit for that, but two that
    share "Z350 XT" do.

    Returns 0.0 if either side has no distinguishing content.
    """
    s_tokens = set(tokenize(search))
    c_tokens = set(tokenize(candidate))
    if not s_tokens or not c_tokens:
        return 0.0
    intersect = s_tokens & c_tokens
    union = s_tokens | c_tokens
    num = sum(_weight(t) for t in intersect)
    den = sum(_weight(t) for t in union)
    return num / den if den > 0 else 0.0


def fuzz_ratio(search: str, candidate: str) -> float:
    """rapidfuzz token_set_ratio normalized to 0..1.

    `token_set_ratio` is order- and case-insensitive and tolerant of extra
    tokens on either side, which fits product names well.
    """
    if not search or not candidate:
        return 0.0
    return fuzz.token_set_ratio(search, candidate) / 100.0
