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
from app.matching.structured import ProductRecord, StructuredVerdict, structured_match
from app.matching.triage import triage_batch
from app.routes.compare import (
    CompareResult,
    CompetitorMatch,
    DkRow,
    _build_dk_result,
    _pick_dk_child,
)
from app.scrapers.bridge import COMPETITORS, fetch_product

router = APIRouter(prefix="/serp", tags=["serp"])


def _no_match(cid: str, cname: str, seen: int) -> CompetitorMatch:
    return CompetitorMatch(
        competitor_id=cid, competitor_name=cname, candidates_seen=seen,
        matched_name=None, matched_url=None, matched_price=None, matched_image=None,
        in_stock=None, verdict=None, score=None, cosine=None,
    )


async def _match_competitor(cid: str, cname: str, url: str | None,
                            ref: ProductRecord, dk_price: float | None) -> CompetitorMatch:
    if not url:
        return _no_match(cid, cname, 0)
    pdp = await fetch_product(cid, url)
    if pdp is None:
        return _no_match(cid, cname, 1)
    pipeline.select_variant(pdp, ref.variant_spec, dk_price, ref.name)
    rec = pipeline.record_from(pdp)
    sm = structured_match(ref, rec)
    if sm.verdict is StructuredVerdict.REJECTED:
        return _no_match(cid, cname, 1)
    tri = triage_batch(ref.name, [rec.name])
    score = max(tri[0].score if tri else 0.0, sm.features.cosine)
    verdict = "confirmed" if sm.verdict is StructuredVerdict.CONFIRMED else "possible"
    diff = round(dk_price - rec.price, 2) if dk_price and rec.price else None
    return CompetitorMatch(
        competitor_id=cid, competitor_name=cname, candidates_seen=1,
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
    urls = await serp.serp_product_urls(name)

    # DentalKart anchor — resolve its PDP (+ sub-variant) via our DK logic.
    dk_match = None
    dk_record = None
    dk_url = urls.get("dentalkart")
    if dk_url:
        dk_pdp = await fetch_product("dentalkart", dk_url)
        if dk_pdp is not None:
            child = _pick_dk_child(name, dk_pdp.name, dk_pdp.variants) if dk_pdp.variants else None
            dk_match, dk_record = _build_dk_result(dk_pdp, child, name)
            dk_match.competitor_id, dk_match.competitor_name = "dentalkart", "Dentalkart"

    ref = dk_record if dk_record is not None else ProductRecord(name=name)
    dk_price = dk_match.matched_price if dk_match else None

    comps = await asyncio.gather(
        *(_match_competitor(cid, cname, urls.get(cid), ref, dk_price)
          for cid, cname in COMPETITORS)
    )
    return CompareResult(dentalkart=DkRow(name=name), dentalkart_match=dk_match,
                         competitors=list(comps))
