"""
Ingest a CSV of Dentalkart product names into the dentalkart_catalog
table, embedding each name with the sentence-transformer model.

Usage:
    uv run python scripts/build_catalog_index.py path/to/products.csv

CSV columns expected (case-insensitive):
    name  (required) - product display name
    sku   (optional) - Dentalkart SKU
    brand (optional) - overrides first-word brand inference
"""
from __future__ import annotations

import argparse
import asyncio
import csv
from pathlib import Path

import numpy as np

from app.db import Database, get_db
from app.matching.embed import get_embedder
from app.matching.normalize import normalize_for_match


def _vec_literal(v: np.ndarray) -> str:
    return "[" + ",".join(f"{float(x):.6f}" for x in v.tolist()) + "]"


async def ingest_csv(path: Path, db: Database, batch: int = 64) -> int:
    emb = get_embedder()
    rows: list[dict[str, str]] = []
    with path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            keyed = {k.lower().strip(): (v or "").strip() for k, v in row.items()}
            if not keyed.get("name"):
                continue
            rows.append(keyed)

    inserted = 0
    for i in range(0, len(rows), batch):
        chunk = rows[i : i + batch]
        names = [r["name"] for r in chunk]
        vecs = emb.encode_many(names)
        for r, v in zip(chunk, vecs, strict=True):
            brand = r.get("brand") or (r["name"].split()[0].lower() if r["name"] else None)
            await db.execute(
                "INSERT INTO dentalkart_catalog (name, normalized, brand, sku, embedding) "
                "VALUES ($1, $2, $3, $4, $5::vector)",
                r["name"],
                normalize_for_match(r["name"]).lower(),
                brand,
                r.get("sku") or None,
                _vec_literal(v),
            )
            inserted += 1
    return inserted


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", type=Path)
    parser.add_argument("--truncate", action="store_true")
    args = parser.parse_args()

    db = await get_db()
    try:
        if args.truncate:
            await db.execute("DELETE FROM dentalkart_catalog")
        n = await ingest_csv(args.csv, db)
        print(f"inserted {n} rows")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(_main())
