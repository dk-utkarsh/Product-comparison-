"""
Sentence-transformer wrapper. Loads the model once (lazy) and returns
L2-normalized vectors so dot-product equals cosine similarity.
"""
from __future__ import annotations

import threading
from functools import lru_cache

import numpy as np
from sentence_transformers import SentenceTransformer

from app.settings import get_settings


class Embedder:
    _lock = threading.Lock()
    _shared_model: SentenceTransformer | None = None

    def __init__(self) -> None:
        self._model: SentenceTransformer = self._get_or_load_model()

    @classmethod
    def _get_or_load_model(cls) -> SentenceTransformer:
        with cls._lock:
            if cls._shared_model is None:
                s = get_settings()
                cls._shared_model = SentenceTransformer(s.embed_model, device=s.embed_device)
            return cls._shared_model

    def encode_one(self, text: str) -> np.ndarray:
        v = self._model.encode([text], normalize_embeddings=True)[0]
        return np.asarray(v, dtype=np.float32)

    def encode_many(self, texts: list[str]) -> np.ndarray:
        vs = self._model.encode(list(texts), normalize_embeddings=True, batch_size=32)
        return np.asarray(vs, dtype=np.float32)


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    return Embedder()
