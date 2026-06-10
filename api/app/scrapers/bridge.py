"""
HTTP bridge to the long-lived Node scrape sidecar.

The sidecar (bridges/scrape-server.ts) loads every TS scraper once and exposes
them over localhost:3100. We hit it with httpx, no subprocess spawn per call.
Start the sidecar yourself with:

    npx tsx api/bridges/scrape-server.ts

(Or use the helper script in `scripts/start-scrape-server.sh`.)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

SCRAPE_SERVER_URL = "http://127.0.0.1:3100"
_SCRAPE_TIMEOUT_S = 25.0
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=SCRAPE_SERVER_URL,
            timeout=_SCRAPE_TIMEOUT_S,
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


@dataclass(slots=True)
class CompetitorProduct:
    name: str
    url: str
    image: str
    price: float
    mrp: float
    discount: float
    packaging: str
    in_stock: bool
    description: str
    source: str
    pack_size: int
    unit_price: float
    sku: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CompetitorProduct:
        return cls(
            name=str(d.get("name", "")),
            url=str(d.get("url", "")),
            image=str(d.get("image", "")),
            price=float(d.get("price") or 0),
            mrp=float(d.get("mrp") or 0),
            discount=float(d.get("discount") or 0),
            packaging=str(d.get("packaging") or ""),
            in_stock=bool(d.get("inStock", False)),
            description=str(d.get("description") or ""),
            source=str(d.get("source") or ""),
            pack_size=int(d.get("packSize") or 1),
            unit_price=float(d.get("unitPrice") or 0),
            sku=d.get("sku"),
        )


async def scrape_competitor(competitor_id: str, query: str) -> list[CompetitorProduct]:
    """Hit the sidecar for one competitor + query. Returns [] on any error."""
    client = _get_client()
    try:
        r = await client.get(f"/{competitor_id}", params={"q": query})
    except (httpx.RequestError, httpx.TimeoutException):
        return []

    if r.status_code != 200:
        return []

    try:
        data = r.json()
    except ValueError:
        return []

    if not isinstance(data, list):
        return []

    return [CompetitorProduct.from_dict(d) for d in data if isinstance(d, dict)]


async def sidecar_health() -> bool:
    client = _get_client()
    try:
        r = await client.get("/health", timeout=2.0)
        return r.status_code == 200
    except (httpx.RequestError, httpx.TimeoutException):
        return False


COMPETITORS: list[tuple[str, str]] = [
    ("pinkblue", "Pinkblue"),
    ("oralkart", "Oralkart"),
    ("dentmark", "Dentmark"),
]

_PRODUCT_TIMEOUT_S = 25.0


async def fetch_product(scraper_id: str, url: str) -> CompetitorProduct | None:
    """Fetch one PDP through the sidecar. Returns None on any failure —
    callers fall back to search-result (thin) data."""
    if not url:
        return None
    client = _get_client()
    try:
        r = await client.get(
            "/product",
            params={"scraper": scraper_id, "url": url},
            timeout=_PRODUCT_TIMEOUT_S,
        )
    except (httpx.RequestError, httpx.TimeoutException):
        return None
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except ValueError:
        return None
    if not isinstance(data, dict) or not data.get("name"):
        return None
    return CompetitorProduct.from_dict(data)
