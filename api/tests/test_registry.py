import asyncio

import pytest

from app import registry
from app.db import get_db
from app.matching.structured import ProductRecord


@pytest.fixture(autouse=True)
def _clean():
    async def _wipe():
        db = await get_db()
        try:
            await db.execute("DELETE FROM product_links")
            await db.execute("DELETE FROM products")
            await db.execute("DELETE FROM competitor_products")
        finally:
            await db.close()
    asyncio.run(_wipe())
    yield
    asyncio.run(_wipe())


def _dk_record():
    return ProductRecord(
        name="GC Fuji IX GP Capsules A2", url="https://www.dentalkart.com/gc-fuji-ix.html",
        description="Glass ionomer", packaging="GC", price=2297, mrp=2500,
        pack_size=50, unit_price=45.9, sku="GC123", source="dentalkart",
    )


def test_upsert_product_is_idempotent():
    async def run():
        db = await get_db()
        try:
            pid1 = await registry.upsert_product(db, _dk_record())
            pid2 = await registry.upsert_product(db, _dk_record())
            return pid1, pid2
        finally:
            await db.close()
    pid1, pid2 = asyncio.run(run())
    assert pid1 is not None and pid1 == pid2


def test_link_roundtrip_and_status_protection():
    async def run():
        db = await get_db()
        try:
            pid = await registry.upsert_product(db, _dk_record())
            await registry.upsert_link(
                db, pid, "pinkblue", "https://pinkblue.in/fuji-ix",
                verdict="confirmed", confidence=0.9, matched_by="rules",
                reason="all attrs equal", llm_response=None,
            )
            # exercises the jsonb write path on an active link
            await registry.upsert_link(
                db, pid, "pinkblue", "https://pinkblue.in/fuji-ix",
                verdict="confirmed", confidence=0.9, matched_by="rules",
                reason="all attrs equal", llm_response={"x": 1},
            )
            links = await registry.get_active_links(db, pid, "pinkblue")
            # human verification survives a later rules re-write
            await registry.set_link_status(
                db, "https://www.dentalkart.com/gc-fuji-ix.html",
                "pinkblue", "https://pinkblue.in/fuji-ix", "human_verified")
            await registry.upsert_link(
                db, pid, "pinkblue", "https://pinkblue.in/fuji-ix",
                verdict="possible", confidence=0.4, matched_by="rules",
                reason="re-run", llm_response=None,
            )
            links2 = await registry.get_active_links(db, pid, "pinkblue")
            return links, links2
        finally:
            await db.close()
    links, links2 = asyncio.run(run())
    assert len(links) == 1 and links[0].verdict == "confirmed"
    assert links2[0].status == "human_verified"
    assert links2[0].verdict == "confirmed"  # not downgraded


def test_killed_links_are_excluded():
    async def run():
        db = await get_db()
        try:
            pid = await registry.upsert_product(db, _dk_record())
            await registry.upsert_link(
                db, pid, "pinkblue", "https://pinkblue.in/wrong",
                verdict="confirmed", confidence=0.9, matched_by="rules",
                reason="", llm_response=None)
            await registry.set_link_status(
                db, "https://www.dentalkart.com/gc-fuji-ix.html",
                "pinkblue", "https://pinkblue.in/wrong", "killed")
            return await registry.get_active_links(db, pid, "pinkblue")
        finally:
            await db.close()
    assert asyncio.run(run()) == []
