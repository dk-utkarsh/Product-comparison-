"""
SerpAPI discovery (isolated, opt-in) — Google finds each competitor's exact PDP.

Given a product name, one SerpAPI `google` search (using the BASE name, so long
descriptive titles still match) returns organic results; we segregate them by
domain into DentalKart + competitors, keeping only real PRODUCT pages (category
/collection/brand listing pages are skipped) and choosing the link whose slug
best matches the product. The caller fetches those PDPs through the normal
sidecar and verifies with the existing matcher — SerpAPI = recall, our pipeline
= precision. A `site:<domain>` query fills competitors missing from the broad
results.

Fully separate from /compare; returns {} when disabled or no key.
"""
from __future__ import annotations

import re

import httpx

from app.matching.variant_spec import base_name
from app.settings import get_settings

_DOMAINS: dict[str, str] = {
    "dentalkart.com": "dentalkart",
    "pinkblue.in": "pinkblue",
    "oralkart.com": "oralkart",
    "dentmark.in": "dentmark",
}
_ENDPOINT = "https://serpapi.com/search.json"
_TIMEOUT_S = 60.0

# URL fragments that mark a NON-product page (category / listing / brand / search).
_NON_PDP = (
    "/collections/", "/collection/", "/brand/", "/brands/", "/c/",
    "/catalog/category", "/pages/", "/page/", "/search", "/sitemap",
    "category=", "/blog", "/cart",
)
# Common category-ish Magento slugs (plural/equipment listing pages) to skip.
_CATEGORY_SLUG = re.compile(
    r"(cabinets?|equipments?|instruments?|materials?|products?|accessories|"
    r"supplies|category|categories|all)$"
)


def _is_pdp(link: str) -> bool:
    low = link.lower()
    if any(p in low for p in _NON_PDP):
        return False
    slug = low.split("?", 1)[0].rstrip("/").split("/")[-1]
    slug = re.sub(r"\.html?$", "", slug)
    # A bare plural-category slug (e.g. "uv-cabinets", "sterilization-equipment")
    # is a listing page, not a product.
    return not _CATEGORY_SLUG.search(slug)


def _toks(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _slug_toks(link: str) -> set[str]:
    slug = link.split("?", 1)[0].rstrip("/").split("/")[-1]
    return _toks(re.sub(r"\.html?$", "", slug))


def _collect(organic: list[dict]) -> dict[str, list[str]]:
    """domain id → candidate PDP links (listing pages filtered out)."""
    cand: dict[str, list[str]] = {}
    for o in organic:
        link = str(o.get("link") or "")
        if not link.startswith("http") or not _is_pdp(link):
            continue
        for domain, cid in _DOMAINS.items():
            if domain in link:
                cand.setdefault(cid, []).append(link)
                break
    return cand


async def _search(client: httpx.AsyncClient, key: str, query: str) -> list[dict]:
    params = {"engine": "google", "q": query, "api_key": key,
              "gl": "in", "hl": "en", "num": "20"}
    try:
        r = await client.get(_ENDPOINT, params=params)
        if r.status_code != 200:
            return []
        return r.json().get("organic_results", []) or []
    except (httpx.HTTPError, ValueError):
        return []


async def serp_product_candidates(name: str) -> dict[str, list[str]]:
    """{ 'dentalkart'|'pinkblue'|… : [pdp_url, …] } — the candidate PDPs per source
    in Google PAGE ORDER (most relevant first), deduped. The caller runs each
    through the matcher and keeps the best-VERIFIED one, instead of us pre-guessing
    a single URL by slug overlap (which can pick the wrong near-duplicate sibling,
    e.g. the EXA6 probe over the EXS6 one). Page order is preserved so the matcher
    can prefer earlier-ranked candidates on a tie. A `site:<domain>` query backfills
    sources missing from the broad search. Costs no extra SerpAPI quota beyond the
    searches already made — the per-candidate PDP fetches go through our scraper."""
    s = get_settings()
    if not s.serp_enabled or not s.serpapi_key or not name:
        return {}
    query = base_name(name) or name          # short query → long titles still match
    name_toks = _toks(name)

    def ordered(links: list[str]) -> list[str]:
        # Dedup on the URL minus its query string (keep earliest occurrence), then
        # stable-sort so links whose slug shares more of the product's tokens float
        # up — Python's sort is stable, so equal-overlap links keep Google's order.
        uniq = list(dict.fromkeys(l.split("?", 1)[0] for l in links))
        return sorted(uniq, key=lambda l: -len(_slug_toks(l) & name_toks))

    out: dict[str, list[str]] = {}
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        for cid, links in _collect(await _search(client, s.serpapi_key, query)).items():
            out[cid] = ordered(links)
        if s.serp_site_fallback:
            for domain, cid in _DOMAINS.items():
                if out.get(cid):
                    continue
                org = await _search(client, s.serpapi_key, f"{query} site:{domain}")
                links = [str(o.get("link") or "") for o in org
                         if domain in str(o.get("link") or "") and _is_pdp(str(o.get("link") or ""))]
                if links:
                    out[cid] = ordered(links)
    return out


async def serp_product_urls(name: str) -> dict[str, str]:
    """{ source : best_pdp_url } — the single top candidate per source (debug /
    back-compat). Prefer serp_product_candidates() for matching."""
    return {cid: links[0] for cid, links in (await serp_product_candidates(name)).items() if links}
