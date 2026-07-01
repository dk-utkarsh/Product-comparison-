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
from urllib.parse import urlparse

import httpx

from app.matching.variant_spec import base_name
from app.settings import get_settings

_DOMAINS: dict[str, str] = {
    "dentalkart.com": "dentalkart",
    "pinkblue.in": "pinkblue",
    "oralkart.com": "oralkart",
    "dentmark.in": "dentmark",
}
# Competitors we have DEDICATED scrapers for (own-site search + PDP fetch). Every
# other merchant surfaced by Google Shopping is fetched through the GENERIC reader.
_KNOWN_CIDS: frozenset[str] = frozenset({"dentalkart", "pinkblue", "oralkart", "dentmark"})
# The baseline competitors we ALWAYS surface (even if absent from Shopping, so the
# user keeps seeing them with a "Not on Google Shopping" note). DK is the anchor.
_BASELINE_CIDS: tuple[str, ...] = ("pinkblue", "oralkart", "dentmark")
_ENDPOINT = "https://serpapi.com/search.json"
_ACCOUNT_ENDPOINT = "https://serpapi.com/account"
_TIMEOUT_S = 60.0


async def serpapi_quota() -> dict:
    """Live SerpAPI monthly quota for the configured key (for the UI credits badge).
    {left, used, total, plan}. `enabled` reflects SERP_ENABLED + a key being set."""
    s = get_settings()
    out: dict = {"enabled": bool(s.serp_enabled and s.serpapi_key)}
    if not s.serpapi_key:
        return out
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(_ACCOUNT_ENDPOINT, params={"api_key": s.serpapi_key})
            r.raise_for_status()
            d = r.json()
        out.update(
            left=d.get("total_searches_left"),
            used=d.get("this_month_usage"),
            total=d.get("searches_per_month"),
            plan=d.get("plan_name"),
        )
    except Exception:
        out["error"] = True
    return out

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


async def serp_shopping_sources(name: str) -> set[str] | None:
    """Competitor IDs that list this product on GOOGLE SHOPPING — or None when the
    lookup itself could NOT be performed.

    Google Shopping aggregates merchant listings; `shopping_results[].source` is the
    merchant ("Oralkart", "Dentmark.com", "Pinkblue"…). Returns the set of OUR
    competitor ids present.

    Return value is THREE-STATE on purpose:
      * a set (possibly with members) → a VALID answer: these competitors are listed.
      * an EMPTY set → valid answer, but none of our competitors were among the
        sellers (genuine "Not on Google Shopping").
      * None → the lookup FAILED (disabled / no key / HTTP error / quota-429 /
        empty payload). The caller MUST NOT claim "Not on Google Shopping" on None.

    The distinction matters: previously a failed/quota-blocked search returned an
    empty set, which the gate read as "every competitor is absent" → the WHOLE run
    showed "Not on Google Shopping". A failure is "unknown", not "absent" — callers
    fail OPEN (match normally) when this is None. One SerpAPI search."""
    s = get_settings()
    if not s.serp_enabled or not s.serpapi_key or not name:
        return None
    query = base_name(name) or name
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            r = await client.get(_ENDPOINT, params={
                "engine": "google_shopping", "q": query,
                "api_key": s.serpapi_key, "gl": "in", "hl": "en"})
            if r.status_code != 200:
                return None            # quota/429/auth/server error — unknown, not absent
            results = r.json().get("shopping_results", []) or []
    except (httpx.HTTPError, ValueError):
        return None                    # network/parse failure — unknown, not absent
    if not results:
        return None                    # no shopping data surfaced — treat as unknown
    found: set[str] = set()
    for p in results:
        src = str(p.get("source") or "").lower()
        for domain, cid in _DOMAINS.items():
            if domain.split(".")[0] in src:   # "dentmark" in "dentmark.com"
                found.add(cid)
    return found


async def serp_product_urls(name: str) -> dict[str, str]:
    """{ source : best_pdp_url } — the single top candidate per source (debug /
    back-compat). Prefer serp_product_candidates() for matching."""
    return {cid: links[0] for cid, links in (await serp_product_candidates(name)).items() if links}


# ───────────────────────── top-N competitors (dynamic) ──────────────────────
# Beyond the 4 fixed competitors, surface the TOP merchants Google Shopping lists
# for a product (amazon.in, thedentistshop.com, Libral Traders, …). Each is then
# verified through the SAME matcher as the known competitors (see routes/serp.py).


def _merchant_key(text: str) -> str:
    """Canonical key for a merchant name OR a host, so a Shopping `source`
    ("PinkBlue.in", "Oralkart") maps to an organic host ("pinkblue.in",
    "oralkart.com"). Strips protocol/path, a trailing TLD, then non-alphanumerics:
    "PinkBlue.in" → "pinkblue", "thedentistshop.com" → "thedentistshop",
    "Libral Traders" → "libraltraders"."""
    s = (text or "").strip().lower()
    s = re.sub(r"^https?://", "", s).split("/", 1)[0]
    s = re.sub(r"\.(com|in|co|net|org|shop|store|biz|io)(\.[a-z]{2})?$", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


def _cid_for_source(source: str) -> tuple[str, str | None, bool]:
    """(cid, domain, is_known) for a Google-Shopping merchant name. Maps to one of
    our 4 dedicated competitors when it matches, else a slug cid for the generic
    path."""
    mkey = _merchant_key(source)
    for domain, cid in _DOMAINS.items():
        droot = _merchant_key(domain)
        if mkey and (mkey == droot or droot in mkey or mkey in droot):
            return cid, domain, True
    return (mkey or re.sub(r"[^a-z0-9]+", "-", source.lower()).strip("-")), None, False


def _collect_all(organic: list[dict]) -> dict[str, list[str]]:
    """host → candidate PDP links, for ALL domains (not just our 4). Listing/
    category pages are filtered out exactly as in _collect."""
    out: dict[str, list[str]] = {}
    for o in organic:
        link = str(o.get("link") or "")
        if not link.startswith("http") or not _is_pdp(link):
            continue
        host = urlparse(link).netloc.lower().removeprefix("www.")
        if host:
            out.setdefault(host, []).append(link)
    return out


async def _serp_json(client: httpx.AsyncClient, url: str, key: str) -> dict:
    """GET a SerpAPI follow-up URL (e.g. the immersive product API) with the key
    appended. Returns {} on any failure."""
    sep = "&" if "?" in url else "?"
    try:
        r = await client.get(f"{url}{sep}api_key={key}")
        return r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        return {}


async def _immersive_link(client: httpx.AsyncClient, immersive_url: str, key: str,
                          mkey: str) -> str | None:
    """Resolve a merchant's DIRECT PDP url via the immersive product API. Shopping
    rows only carry a Google redirect; product_results.stores carries each seller's
    real link. Pick the store matching this merchant, else the first."""
    if not immersive_url:
        return None
    d = await _serp_json(client, immersive_url, key)
    stores = (d.get("product_results", {}) or {}).get("stores") or d.get("stores") or []
    if not stores:
        return None
    best = next((s for s in stores if _merchant_key(str(s.get("name") or "")) == mkey), None)
    best = best or stores[0]
    link = str(best.get("link") or best.get("direct_link") or "")
    return link or None


async def serp_top_competitors(name: str, limit: int = 10) -> list[dict] | None:
    """Top `limit` merchants Google Shopping lists for this product, each enriched
    with a directly-fetchable PDP url so the caller can verify it through the SAME
    matcher used for the known competitors.

    Returns a list of merchant dicts ordered by Shopping rank, or None when the
    Shopping lookup itself FAILED (quota/429/error/empty) — the caller then falls
    back to the legacy fixed-competitor path rather than showing nothing.

    Each merchant: {cid, name, domain, known, on_shopping, price, thumbnail,
    pdp_url, candidates}. `candidates` are the free organic PDP urls for that host
    (used for the known competitors); `pdp_url` is the resolved direct url (free
    from organic, else 1 immersive search for an unknown merchant). The 3 baseline
    competitors are always present (on_shopping=False placeholder if absent)."""
    s = get_settings()
    if not s.serp_enabled or not s.serpapi_key or not name:
        return None
    query = base_name(name) or name
    name_toks = _toks(name)

    def ordered(links: list[str]) -> list[str]:
        uniq = list(dict.fromkeys(l.split("?", 1)[0] for l in links))
        return sorted(uniq, key=lambda l: -len(_slug_toks(l) & name_toks))

    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        # 1) ORGANIC (free) — PDP urls for ANY domain, reused for known + new.
        organic = _collect_all(await _search(client, s.serpapi_key, query))
        organic_by_key: dict[str, list[str]] = {}
        for host, links in organic.items():
            organic_by_key.setdefault(_merchant_key(host), []).extend(links)

        # 2) SHOPPING — the ranked merchant list (this is the gate too).
        try:
            r = await client.get(_ENDPOINT, params={
                "engine": "google_shopping", "q": query,
                "api_key": s.serpapi_key, "gl": "in", "hl": "en"})
            if r.status_code != 200:
                return None
            rows = r.json().get("shopping_results", []) or []
        except (httpx.HTTPError, ValueError):
            return None
        if not rows:
            return None

        # Dedupe merchants in Shopping order; skip DK (it's the anchor, resolved
        # separately by the route).
        merchants: list[dict] = []
        seen: set[str] = set()
        for row in rows:
            src = str(row.get("source") or "").strip()
            cid, domain, known = _cid_for_source(src)
            mkey = _merchant_key(src)
            if not mkey or cid == "dentalkart" or mkey in seen:
                continue
            seen.add(mkey)
            cand = ordered(organic_by_key.get(mkey, []))
            merchants.append({
                "cid": cid, "name": src, "domain": domain, "known": known,
                "on_shopping": True, "price": row.get("extracted_price"),
                "thumbnail": row.get("thumbnail"),
                "card_title": str(row.get("title") or ""),   # for title-level verify
                "pdp_url": cand[0] if cand else None, "candidates": cand,
                "_mkey": mkey, "_immersive": row.get("serpapi_immersive_product_api"),
            })

        selected = merchants[:limit]
        # Always keep the 3 baseline competitors visible.
        for cid in _BASELINE_CIDS:
            if any(m["cid"] == cid for m in selected):
                continue
            found = next((m for m in merchants if m["cid"] == cid), None)
            if found is not None:
                selected.append(found)
            else:
                domain = next((d for d, c in _DOMAINS.items() if c == cid), None)
                selected.append({
                    "cid": cid, "name": cid.capitalize(), "domain": domain,
                    "known": True, "on_shopping": False, "price": None,
                    "thumbnail": None, "pdp_url": None, "candidates": [],
                    "_mkey": cid, "_immersive": None,
                })

        # 3) Resolve a direct PDP url for any NEW merchant we couldn't get for free
        #    (verify-all). 1 immersive search each — known competitors skip this
        #    (they use their own-site search downstream).
        for m in selected:
            if (not m["known"] and m["on_shopping"]
                    and not m["pdp_url"] and m["_immersive"]):
                m["pdp_url"] = await _immersive_link(
                    client, m["_immersive"], s.serpapi_key, m["_mkey"])
        return selected
