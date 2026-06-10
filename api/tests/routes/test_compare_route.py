"""End-to-end /compare/single with the bridge and judge stubbed out.
Hits the real DB (like the feedback tests) for the registry side."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app import pipeline
from app.db import get_db
from app.main import app
from app.scrapers import bridge
from app.scrapers.bridge import CompetitorProduct


def _cp(name, url, price, source, description="", packaging="", pack_size=1):
    return CompetitorProduct(
        name=name, url=url, image="", price=price, mrp=price, discount=0,
        packaging=packaging, in_stock=True, description=description,
        source=source, pack_size=pack_size,
        unit_price=price / max(pack_size, 1), sku=None,
    )


DK_SEARCH = [_cp("GC Fuji IX GP Capsules A2", "https://www.dentalkart.com/fuji-ix.html",
                 2297, "dentalkart", description="Glass ionomer")]
PB_SEARCH = [_cp("GC Fuji 9 GP Caps Shade A2", "https://pinkblue.in/fuji-ix", 2236,
                 "pinkblue")]
PB_PDP = _cp("GC Fuji 9 GP Caps Shade A2", "https://pinkblue.in/fuji-ix", 2236,
             "pinkblue", description="Glass ionomer capsules, shade A2",
             packaging="Shade: A2")


@pytest.fixture(autouse=True)
def _clean_registry():
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


@pytest.fixture(autouse=True)
def _stub_scrapers(monkeypatch):
    async def fake_search(cid, query):
        if cid == "dentalkart":
            return list(DK_SEARCH)
        if cid == "pinkblue":
            return list(PB_SEARCH)
        return []

    async def fake_pdp(cid, url):
        if cid == "pinkblue" and url == "https://pinkblue.in/fuji-ix":
            return PB_PDP
        if cid == "dentalkart":
            return DK_SEARCH[0]
        return None

    # compare.py and pipeline.py both import these names — patch every site.
    monkeypatch.setattr(bridge, "scrape_competitor", fake_search)
    monkeypatch.setattr(bridge, "fetch_product", fake_pdp)
    monkeypatch.setattr(pipeline, "scrape_competitor", fake_search)
    monkeypatch.setattr(pipeline, "fetch_product", fake_pdp)
    import app.routes.compare as compare_mod
    monkeypatch.setattr(compare_mod, "scrape_competitor", fake_search)
    monkeypatch.setattr(compare_mod, "fetch_product", fake_pdp, raising=False)


def test_compare_single_matches_and_persists_link():
    client = TestClient(app)
    res = client.post("/compare/single", json={"name": "GC Fuji IX GP Capsules A2"})
    assert res.status_code == 200
    j = res.json()
    pb = next(c for c in j["competitors"] if c["competitor_id"] == "pinkblue")
    assert pb["matched_url"] == "https://pinkblue.in/fuji-ix"
    assert pb["verdict"] in ("confirmed", "possible")
    assert pb["matched_by"] in ("rules", "llm")

    async def count_links():
        db = await get_db()
        try:
            row = await db.fetchrow("SELECT count(*) AS c FROM product_links")
            return int(row["c"])
        finally:
            await db.close()
    assert asyncio.run(count_links()) >= 1


def test_compare_single_uses_registry_on_second_run():
    client = TestClient(app)
    client.post("/compare/single", json={"name": "GC Fuji IX GP Capsules A2"})
    res2 = client.post("/compare/single", json={"name": "GC Fuji IX GP Capsules A2"})
    pb = next(c for c in res2.json()["competitors"] if c["competitor_id"] == "pinkblue")
    assert pb["matched_by"] == "registry"
