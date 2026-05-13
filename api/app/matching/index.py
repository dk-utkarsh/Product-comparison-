"""
Catalog vector index. Uses pgvector's cosine operator (<=>) for top-K
recall over the dentalkart_catalog table. FAISS-free for now; we'll
introduce FAISS only if pgvector becomes a bottleneck at scale.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.db import Database
from app.matching.embed import get_embedder


@dataclass(slots=True)
class CatalogHit:
    id: int
    name: str
    sku: str | None
    brand: str | None
    distance: float

    @property
    def cosine(self) -> float:
        return 1.0 - self.distance


def _vec_literal(v: np.ndarray) -> str:
    return "[" + ",".join(f"{float(x):.6f}" for x in v.tolist()) + "]"


class CatalogIndex:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def top_k(self, query: str, k: int = 50) -> list[CatalogHit]:
        emb = get_embedder()
        vec = emb.encode_one(query)
        rows = await self._db.fetch(
            """
            SELECT id, name, sku, brand,
                   embedding <=> $1::vector AS distance
            FROM dentalkart_catalog
            ORDER BY embedding <=> $1::vector
            LIMIT $2
            """,
            _vec_literal(vec),
            k,
        )
        return [
            CatalogHit(
                id=r["id"],
                name=r["name"],
                sku=r["sku"],
                brand=r["brand"],
                distance=float(r["distance"]),
            )
            for r in rows
        ]
