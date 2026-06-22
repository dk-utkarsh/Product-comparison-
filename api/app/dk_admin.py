"""
DentalKart admin catalog client — pulls random SKUs for scheduled runs.

The admin SPA's product list is a NestJS endpoint authenticated by a frontend
`x-api-key` (no user login). It returns the full catalog paged, each product
carrying `sku` and an `attributes` dict with `name`/`price`/`url_key`. We sample
random pages and keep only real, sellable products (enabled + has a name).
"""
from __future__ import annotations

import random
from dataclasses import dataclass

import httpx

from app.matching.normalize import fix_mojibake
from app.settings import get_settings

_PAGE_SIZE = 100
_TIMEOUT_S = 30.0


@dataclass(slots=True)
class AdminProduct:
    sku: str   # identifier for the run record
    name: str  # the ONLY thing the comparison pipeline needs


def _valid(p: dict) -> AdminProduct | None:
    """Keep enabled products that actually have a name (skip disabled/blank rows).
    We deliberately pull only the NAME — the tool resolves the DK product and its
    real price itself, so the admin price is neither needed nor used."""
    if not isinstance(p, dict) or p.get("status") != 1:
        return None
    sku = str(p.get("sku") or "").strip()
    attrs = p.get("attributes") if isinstance(p.get("attributes"), dict) else {}
    name = fix_mojibake(str(attrs.get("name") or "").strip())
    if not sku or not name:
        return None
    type_id = str(p.get("type_id") or "")
    if type_id not in ("simple", "configurable", "virtual", "bundle", "grouped", ""):
        return None
    return AdminProduct(sku=sku, name=name)


async def _fetch_page(client: httpx.AsyncClient, url: str, key: str, page: int) -> tuple[list[dict], int]:
    r = await client.post(
        url,
        headers={"x-api-key": key, "Content-Type": "application/json"},
        json={"page": page, "pageSize": _PAGE_SIZE},
    )
    r.raise_for_status()
    data = r.json()
    return data.get("products", []) or [], int(data.get("totalPages") or 1)


async def fetch_random_skus(n: int, *, seed: int | None = None) -> list[AdminProduct]:
    """Return up to `n` random enabled products (sku + name + price) from the
    admin catalog. Samples random pages until it has enough, then shuffles."""
    s = get_settings()
    if not s.dk_admin_api_key:
        return []
    rng = random.Random(seed)
    out: dict[str, AdminProduct] = {}
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        # First page tells us how many pages exist.
        first, total_pages = await _fetch_page(client, s.dk_admin_products_url, s.dk_admin_api_key, 1)
        for p in first:
            if (vp := _valid(p)) is not None:
                out[vp.sku] = vp
        pages = list(range(1, max(total_pages, 1) + 1))
        rng.shuffle(pages)
        # Pull random pages until we have enough valid products (bounded).
        for page in pages:
            if len(out) >= n * 2 or len(out) >= n + 30:
                break
            try:
                prods, _ = await _fetch_page(client, s.dk_admin_products_url, s.dk_admin_api_key, page)
            except (httpx.HTTPError, ValueError):
                continue
            for p in prods:
                if (vp := _valid(p)) is not None:
                    out[vp.sku] = vp
    picked = list(out.values())
    rng.shuffle(picked)
    return picked[:n]
