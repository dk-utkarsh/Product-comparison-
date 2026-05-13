import asyncio

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app


@pytest.fixture(autouse=True)
def _clean_feedback():
    async def _wipe():
        db = await get_db()
        try:
            await db.execute("DELETE FROM match_feedback")
        finally:
            await db.close()
    asyncio.run(_wipe())
    yield
    asyncio.run(_wipe())


def _payload(**over):
    base = {
        "search_term": "Wizdent Master Design Refill - A3B",
        "competitor_id": "pinkblue",
        "matched_name": "Wizdent Master Design Refills",
        "matched_url": "https://pinkblue.in/x",
        "matched_price": 2236,
        "dk_price": 2297,
        "score": 0.91,
        "cosine": 0.84,
        "verdict": "confirmed",
        "reasons": "cosine=0.84",
        "was_correct": True,
    }
    base.update(over)
    return base


def test_post_feedback_inserts_and_returns_total():
    client = TestClient(app)
    res = client.post("/feedback", json=_payload())
    assert res.status_code == 200
    j = res.json()
    assert j["status"] == "ok"
    assert j["total"] == 1

    res2 = client.post("/feedback", json=_payload(was_correct=False))
    assert res2.json()["total"] == 2


def test_feedback_stats():
    client = TestClient(app)
    client.post("/feedback", json=_payload(was_correct=True))
    client.post("/feedback", json=_payload(was_correct=True))
    client.post("/feedback", json=_payload(was_correct=False))

    stats = client.get("/feedback/stats").json()
    assert stats == {"total": 3, "correct": 2, "incorrect": 1}


def test_post_feedback_rejects_empty_search():
    client = TestClient(app)
    res = client.post("/feedback", json=_payload(search_term=""))
    assert res.status_code == 422
