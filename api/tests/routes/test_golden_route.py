import asyncio

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app


@pytest.fixture(autouse=True)
def _clean():
    async def _wipe():
        db = await get_db()
        try:
            await db.execute("DELETE FROM golden_links")
        finally:
            await db.close()
    asyncio.run(_wipe())
    yield
    asyncio.run(_wipe())


def test_golden_correct_and_no_match_roundtrip():
    client = TestClient(app)
    r1 = client.post("/golden", json={
        "dk_name": "GC Fuji IX A2", "source": "pinkblue",
        "competitor_url": "https://pinkblue.in/fuji", "label": "correct"})
    assert r1.status_code == 200
    r2 = client.post("/golden", json={
        "dk_name": "GC Fuji IX A2", "source": "oralkart",
        "competitor_url": None, "label": "no_match"})
    assert r2.status_code == 200
    assert client.get("/golden/count").json()["count"] == 2


def test_golden_upsert_replaces_same_pair():
    client = TestClient(app)
    for url in ("https://pinkblue.in/a", "https://pinkblue.in/b"):
        client.post("/golden", json={
            "dk_name": "X", "source": "pinkblue",
            "competitor_url": url, "label": "correct"})
    assert client.get("/golden/count").json()["count"] == 1


def test_golden_rejects_bad_label():
    client = TestClient(app)
    r = client.post("/golden", json={
        "dk_name": "X", "source": "pinkblue",
        "competitor_url": "https://x", "label": "maybe"})
    assert r.status_code == 422
