import pytest

from app.db import get_db


@pytest.mark.asyncio
async def test_db_returns_one_row():
    db = await get_db()
    rows = await db.fetch("SELECT 1 AS one")
    assert rows[0]["one"] == 1
    await db.close()


@pytest.mark.asyncio
async def test_db_fetchrow():
    db = await get_db()
    row = await db.fetchrow("SELECT 2 AS two")
    assert row is not None
    assert row["two"] == 2
    await db.close()
