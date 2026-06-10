"""
Persistent match registry — products, competitor_products, product_links.

Each function takes an open Database and is safe to call with degraded
expectations: callers wrap registry usage in try/except and fall back to
stateless matching when the DB is unavailable.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.db import Database
from app.matching.structured import ProductRecord


@dataclass(slots=True)
class Link:
    id: int
    product_id: int
    source: str
    competitor_url: str
    verdict: str
    confidence: float
    matched_by: str
    reason: str | None
    status: str


async def upsert_product(db: Database, rec: ProductRecord) -> int | None:
    """Insert/refresh a scraped Dentalkart PDP keyed by URL. Returns id."""
    if not rec.url:
        return None
    row = await db.fetchrow(
        """
        INSERT INTO products (sku, url, name, description, packaging,
                              price, mrp, pack_size, scraped_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8, now())
        ON CONFLICT (url) DO UPDATE SET
          name = EXCLUDED.name, description = EXCLUDED.description,
          packaging = EXCLUDED.packaging, price = EXCLUDED.price,
          mrp = EXCLUDED.mrp, pack_size = EXCLUDED.pack_size,
          sku = EXCLUDED.sku, scraped_at = now()
        RETURNING id
        """,
        rec.sku, rec.url, rec.name, rec.description, rec.packaging,
        rec.price, rec.mrp, rec.pack_size,
    )
    return int(row["id"]) if row else None


async def upsert_competitor_product(db: Database, rec: ProductRecord) -> None:
    if not rec.url:
        return
    await db.execute(
        """
        INSERT INTO competitor_products (source, url, name, description,
                                         packaging, price, mrp, pack_size, scraped_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8, now())
        ON CONFLICT (url) DO UPDATE SET
          name = EXCLUDED.name, description = EXCLUDED.description,
          packaging = EXCLUDED.packaging, price = EXCLUDED.price,
          mrp = EXCLUDED.mrp, pack_size = EXCLUDED.pack_size, scraped_at = now()
        """,
        rec.source, rec.url, rec.name, rec.description, rec.packaging,
        rec.price, rec.mrp, rec.pack_size,
    )


async def upsert_link(
    db: Database, product_id: int, source: str, competitor_url: str, *,
    verdict: str, confidence: float, matched_by: str,
    reason: str | None, llm_response: dict[str, Any] | None,
) -> None:
    """Write a match decision. NEVER touches rows a human has settled
    (status human_verified/killed stay as-is, including their verdict)."""
    await db.execute(
        """
        INSERT INTO product_links (product_id, source, competitor_url, verdict,
                                   confidence, matched_by, reason, llm_response)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        ON CONFLICT (product_id, source, competitor_url) DO UPDATE SET
          verdict = EXCLUDED.verdict, confidence = EXCLUDED.confidence,
          matched_by = EXCLUDED.matched_by, reason = EXCLUDED.reason,
          llm_response = COALESCE(EXCLUDED.llm_response, product_links.llm_response),
          updated_at = now()
        WHERE product_links.status = 'active'
        """,
        product_id, source, competitor_url, verdict, confidence,
        matched_by, reason, json.dumps(llm_response) if llm_response else None,
    )


async def get_active_links(db: Database, product_id: int, source: str) -> list[Link]:
    """Usable links for refresh: not killed, verdict confirmed/variant/possible."""
    rows = await db.fetch(
        """
        SELECT id, product_id, source, competitor_url, verdict, confidence,
               matched_by, reason, status
        FROM product_links
        WHERE product_id = $1 AND source = $2 AND status != 'killed'
          AND verdict IN ('confirmed', 'variant', 'possible')
        ORDER BY (status = 'human_verified') DESC, confidence DESC
        """,
        product_id, source,
    )
    return [
        Link(
            id=int(r["id"]), product_id=int(r["product_id"]), source=r["source"],
            competitor_url=r["competitor_url"], verdict=r["verdict"],
            confidence=float(r["confidence"]), matched_by=r["matched_by"],
            reason=r["reason"], status=r["status"],
        )
        for r in rows
    ]


async def find_product_id(db: Database, dk_url: str) -> int | None:
    row = await db.fetchrow("SELECT id FROM products WHERE url = $1", dk_url)
    return int(row["id"]) if row else None


async def set_link_status(
    db: Database, dk_url: str, source: str, competitor_url: str, status: str
) -> bool:
    """Feedback hook: 'human_verified' (👍) or 'killed' (👎), keyed by the
    Dentalkart product URL the UI saw. Returns True when a row changed."""
    pid = await find_product_id(db, dk_url)
    if pid is None:
        return False
    result = await db.execute(
        """
        UPDATE product_links
        SET status = $4, matched_by = 'human', updated_at = now()
        WHERE product_id = $1 AND source = $2 AND competitor_url = $3
        """,
        pid, source, competitor_url, status,
    )
    return result.endswith("1")
