"""
Local Dentalkart catalog search — recovers products DK's on-site search won't
return (recall floor), e.g. "Meril Filasilk #2-0". Searches the full ~8k-product
index built from the sitemaps (scripts/build_dk_catalog.py) by embedding the
query and taking nearest neighbours; the caller then scrapes the chosen URL.

Pure numpy dot-product over an in-memory matrix — no DB. Returns [] gracefully
when the index file is absent (so the tool still runs without it).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np

from app.matching.embed import get_embedder
from app.matching.normalize import normalize_for_match

_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "dk_catalog.npz"


@lru_cache(maxsize=1)
def _load() -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    if not _PATH.exists():
        return None
    d = np.load(_PATH, allow_pickle=True)
    return d["embeddings"], d["urls"], d["names"]


def available() -> bool:
    return _load() is not None


def search(query: str, k: int = 12) -> list[tuple[str, str, float]]:
    """Top-k catalog matches for `query` as (url, name, cosine), best first."""
    data = _load()
    if data is None:
        return []
    emb, urls, names = data
    q = get_embedder().encode_one(normalize_for_match(query) or query)
    sims = emb @ q
    k = min(k, len(sims))
    idx = np.argpartition(-sims, k - 1)[:k]
    idx = idx[np.argsort(-sims[idx])]
    return [(str(urls[i]), str(names[i]), float(sims[i])) for i in idx]
