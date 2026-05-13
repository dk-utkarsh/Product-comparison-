import csv
from pathlib import Path

import pytest

from app.db import get_db
from scripts.build_catalog_index import ingest_csv


@pytest.mark.asyncio
async def test_ingest_csv_writes_rows(tmp_path: Path):
    p = tmp_path / "products.csv"
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "sku"])
        w.writerow(["3M Filtek Z350 XT", "FILTEK-001"])
        w.writerow(["GC Fuji IX GP Capsules", "FUJI-IX-001"])

    db = await get_db()
    try:
        await db.execute("DELETE FROM dentalkart_catalog")
        n = await ingest_csv(p, db)
        assert n == 2
        rows = await db.fetch("SELECT name FROM dentalkart_catalog ORDER BY name")
        assert [r["name"] for r in rows] == [
            "3M Filtek Z350 XT",
            "GC Fuji IX GP Capsules",
        ]
    finally:
        await db.execute("DELETE FROM dentalkart_catalog")
        await db.close()
