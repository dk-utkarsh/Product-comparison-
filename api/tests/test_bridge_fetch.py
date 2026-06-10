import asyncio

import pytest

from app.scrapers import bridge


class _Resp:
    def __init__(self, status: int, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class _StubClient:
    def __init__(self, resp):
        self._resp = resp
        self.calls: list[tuple] = []

    async def get(self, path, params=None, timeout=None):
        self.calls.append((path, params))
        return self._resp


@pytest.fixture(autouse=True)
def _reset_client(monkeypatch):
    yield
    monkeypatch.setattr(bridge, "_client", None, raising=False)


def test_fetch_product_parses_pdp(monkeypatch):
    payload = {
        "name": "GC Fuji IX GP Capsules A2",
        "url": "https://pinkblue.in/gc-fuji-ix",
        "image": "", "price": 2300, "mrp": 2500, "discount": 8,
        "packaging": "Shade: A2 | Pack: 50", "inStock": True,
        "description": "Glass ionomer restorative", "source": "pinkblue",
        "packSize": 50, "unitPrice": 46, "sku": "GC123",
    }
    stub = _StubClient(_Resp(200, payload))
    monkeypatch.setattr(bridge, "_client", stub)

    p = asyncio.run(bridge.fetch_product("pinkblue", "https://pinkblue.in/gc-fuji-ix"))
    assert p is not None
    assert p.name == "GC Fuji IX GP Capsules A2"
    assert p.pack_size == 50
    assert stub.calls[0][0] == "/product"
    assert stub.calls[0][1]["scraper"] == "pinkblue"


def test_fetch_product_404_returns_none(monkeypatch):
    monkeypatch.setattr(bridge, "_client", _StubClient(_Resp(404, {"error": "x"})))
    assert asyncio.run(bridge.fetch_product("pinkblue", "https://x")) is None


def test_fetch_product_empty_url_returns_none():
    assert asyncio.run(bridge.fetch_product("pinkblue", "")) is None
