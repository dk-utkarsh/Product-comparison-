"""
SerpAPI discovery route (isolated, opt-in) — Google finds each competitor's PDP,
our normal matcher verifies. Returns the SAME CompareResult shape as /compare so
the existing UI renders it unchanged. Does NOT touch the /compare pipeline.

GET /serp/compare?name=…
GET /serp/urls?name=…      (debug: raw discovered URLs by source)
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter

from app import pipeline, serp
from app.matching.normalize import normalize_for_match
from app.matching.query_builder import ProductContext, extract_smart_queries
from app.matching.structured import ProductRecord, StructuredVerdict, structured_match
from app.matching.tokens import fuzz_ratio
from app.matching.triage import triage_batch
from app.routes.compare import (
    CompareResult,
    CompetitorMatch,
    DkRow,
    _dk_has_input_product,
    _resolve_dk,
)
from app.scrapers.bridge import COMPETITORS, fetch_product, scrape_competitor

router = APIRouter(prefix="/serp", tags=["serp"])


def _no_match(cid: str, cname: str, seen: int, note: str | None = None) -> CompetitorMatch:
    return CompetitorMatch(
        competitor_id=cid, competitor_name=cname, candidates_seen=seen,
        matched_name=None, matched_url=None, matched_price=None, matched_image=None,
        in_stock=None, verdict=None, score=None, cosine=None, note=note,
    )


# How many top candidate PDPs per source to verify through the matcher.
_MAX_CANDIDATES = 6


async def _match_competitor(cid: str, cname: str, urls: list[str],
                            ref: ProductRecord, dk_price: float | None) -> CompetitorMatch:
    """Verify candidate PDPs for this source through the SAME matcher used by
    /compare — brand gate → structured match (name/code/sub-variant) — and keep
    the best. Candidates = Google's discovered URLs PLUS the competitor's OWN
    search results: Google's index of these niche sites is incomplete (it missed
    pinkblue entirely and returned only oralkart's pricier "with Intensity Meter"
    upgrade, not the ₹6500 base), so merging both lets the matcher pick the right
    base variant. Earlier candidates win ties."""
    # Pull the competitor's own search hits with CANONICAL-CORE queries (brand +
    # line + key tokens), NOT the full verbose name. A long name like "3M ESPE
    # Ketac Molar … (15g Powder + 7.8mL Liquid + Mixing Pad + Scoop)" returns 0
    # hits on the competitor's search engine; the short core queries return the
    # right product. Rank the hits by name similarity to the input so the best
    # few survive the _MAX_CANDIDATES cap. (Google URLs lead — they're targeted.)
    own_ranked: list[str] = []
    try:
        ctx = ProductContext(description=ref.description or None,
                             packaging=ref.packaging or None, sku=ref.sku)
        queries = extract_smart_queries(ref.name, ctx) or [ref.name]
        pool: dict[str, object] = {}
        for q in queries[:3]:
            for c in await scrape_competitor(cid, q):
                key = (c.url or "").split("?", 1)[0]
                if key and c.name and key not in pool:
                    pool[key] = c
        ref_norm = normalize_for_match(ref.name)
        ranked = sorted(pool.values(),
                        key=lambda c: fuzz_ratio(ref_norm, normalize_for_match(c.name)),
                        reverse=True)
        own_ranked = [c.url for c in ranked]
    except Exception:  # own-search is best-effort; Google URLs still stand
        own_ranked = []
    seen_urls: set[str] = set()
    cands: list[str] = []
    for u in list(urls) + own_ranked:
        key = u.split("?", 1)[0]
        if key not in seen_urls:
            seen_urls.add(key)
            cands.append(u)
    if not cands:
        return _no_match(cid, cname, 0)

    async def evaluate(url: str) -> tuple[float, str, object, object] | None:
        pdp = await fetch_product(cid, url)
        if pdp is None:
            return None
        pipeline.select_variant(pdp, ref.variant_spec, dk_price, ref.name)
        rec = pipeline.record_from(pdp)
        sm = structured_match(ref, rec)
        if sm.verdict is StructuredVerdict.REJECTED:
            return None  # gate / code / type rejected this candidate
        tri = triage_batch(ref.name, [rec.name])
        score = max(tri[0].score if tri else 0.0, sm.features.cosine)
        return (score, "confirmed" if sm.verdict is StructuredVerdict.CONFIRMED else "possible",
                pdp, sm)

    cands = cands[:_MAX_CANDIDATES]
    seen = len(cands)
    results = await asyncio.gather(*(evaluate(u) for u in cands))
    scored = [r for r in results if r is not None]
    if not scored:
        return _no_match(cid, cname, seen)
    # Prefer CONFIRMED over possible, then higher score. gather preserves page
    # order, and max() keeps the FIRST max on ties → earlier Google rank wins.
    best = max(scored, key=lambda r: (r[1] == "confirmed", r[0]))
    score, verdict, pdp, sm = best
    rec = pipeline.record_from(pdp)
    # SerpAPI already pinned this EXACT PDP on the competitor's own site, and it
    # cleared both the gates and the structured match (not REJECTED) — three
    # independent identity checks. That discovery signal is strong, so surface
    # even a moderate-cosine "possible" match instead of letting the UI's 0.70
    # confidence cutoff hide it. Otherwise a correct sub-variant whose name is
    # merely reworded (pinkblue "Ora Craft Screening Single End (WHO Probe)" vs
    # "Oracraft … WHO Screening Probe #3 - PCP11.5B", cosine ~0.68) is dropped.
    # The true cosine is still carried in `cosine` for transparency. The
    # precision guard is the gate (brand / model-code / tip), which already
    # rejects the wrong sibling ("…Probe #3 - EXS6") before we get here.
    score = max(score, 0.70)
    diff = round(dk_price - rec.price, 2) if dk_price and rec.price else None
    return CompetitorMatch(
        competitor_id=cid, competitor_name=cname, candidates_seen=seen,
        matched_name=rec.name, matched_url=pdp.url, matched_price=rec.price,
        matched_image=pdp.image, in_stock=pdp.in_stock, verdict=verdict,
        score=score, cosine=sm.features.cosine, reasons=list(sm.reasons),
        price_diff_vs_dk=diff, pack_note=sm.pack_note, spec_match=sm.spec_match,
    )


@router.get("/urls")
async def serp_urls(name: str) -> dict:
    return {"name": name, "urls": await serp.serp_product_urls(name)}


@router.get("/compare", response_model=CompareResult)
async def serp_compare(name: str) -> CompareResult:
    cands = await serp.serp_product_candidates(name)

    # DentalKart anchor — resolve via OUR OWN DK search, NOT Google. Google's
    # relevance often returns the wrong DK page (e.g. "Life Steriware … Storage
    # Cabinet" for a "Life Stericab … UV Chamber"), and that page may even fail to
    # fetch — leaving DK empty. DK's own site search nails it (₹8200 here). Google
    # is only the better finder for the harder-to-search COMPETITORS below.
    dk_match, dk_record = await _resolve_dk(DkRow(name=name))
    if dk_match is not None and not _dk_has_input_product(name, dk_record):
        dk_match, dk_record = None, None   # DK resolved to a different variant
    if dk_match is not None:
        dk_match.competitor_id, dk_match.competitor_name = "dentalkart", "Dentalkart"

    ref = dk_record if dk_record is not None else ProductRecord(name=name)
    dk_price = dk_match.matched_price if dk_match else None

    # GATE: only show a competitor if Google Shopping lists them for this product.
    # If not, show "Not on Google Shopping" instead of any match.
    shopping = await serp.serp_shopping_sources(name)

    async def _one(cid: str, cname: str) -> CompetitorMatch:
        if cid not in shopping:
            return _no_match(cid, cname, 0, note="Not on Google Shopping")
        return await _match_competitor(cid, cname, cands.get(cid) or [], ref, dk_price)

    comps = await asyncio.gather(*(_one(cid, cname) for cid, cname in COMPETITORS))
    return CompareResult(dentalkart=DkRow(name=name), dentalkart_match=dk_match,
                         competitors=list(comps))
