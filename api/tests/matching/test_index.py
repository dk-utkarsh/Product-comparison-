import pytest

from app.db import get_db
from app.matching.embed import get_embedder
from app.matching.index import CatalogIndex


@pytest.mark.asyncio
async def test_index_search_returns_topk():
    db = await get_db()
    try:
        emb = get_embedder()
        await db.execute("DELETE FROM dentalkart_catalog")
        names = [
            "3M Filtek Z350 XT Shade A2",
            "3M Filtek Z350 XT Shade A3",
            "GC Fuji IX GP Capsules",
            "Dentsply ProTaper Universal F2",
        ]
        vs = emb.encode_many(names)
        for n, v in zip(names, vs, strict=True):
            await db.execute(
                "INSERT INTO dentalkart_catalog (name, normalized, brand, embedding) "
                "VALUES ($1, $2, $3, $4::vector)",
                n,
                n.lower(),
                n.split()[0].lower(),
                "[" + ",".join(f"{x:.6f}" for x in v.tolist()) + "]",
            )

        idx = CatalogIndex(db)
        hits = await idx.top_k("Filtek Z350 A2", k=2)
        assert len(hits) == 2
        assert "Filtek" in hits[0].name
    finally:
        await db.execute("DELETE FROM dentalkart_catalog")
        await db.close()
