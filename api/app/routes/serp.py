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
from app.settings import get_settings

router = APIRouter(prefix="/serp", tags=["serp"])


def _no_match(cid: str, cname: str, seen: int, note: str | None = None) -> CompetitorMatch:
    return CompetitorMatch(
        competitor_id=cid, competitor_name=cname, candidates_seen=seen,
        matched_name=None, matched_url=None, matched_price=None, matched_image=None,
        in_stock=None, verdict=None, score=None, cosine=None, note=note,
    )


# How many top candidate PDPs per source to verify through the matcher.
_MAX_CANDIDATES = 6

# Sentinel: a candidate page WAS read but our matcher judged it a different
# product (vs None = the page couldn't be read at all). Lets us tell a real
# "no match" apart from "couldn't verify".
_REJECTED = "rejected"


def _card_match(cid: str, cname: str, seen: int, card_price: float | None,
                thumbnail: str | None, dk_price: float | None,
                card_title: str | None, ref: ProductRecord) -> CompetitorMatch:
    """Fallback when we couldn't read the merchant's PDP.

    POLICY (strict): we NEVER show a price we couldn't verify on the seller's own
    page — the Google card alone (title + price) can't confirm page-only details
    (pack size, spec sheet). So this always returns a no-match WITHOUT a price; it
    only refines the NOTE: if the card title or price already prove it's a
    different product, say so; otherwise report that the page couldn't be verified.
    """
    if card_title:
        sm = structured_match(ref, ProductRecord(name=card_title))
        if sm.verdict is StructuredVerdict.REJECTED:
            return _no_match(cid, cname, seen, note="Different product (Google listing)")
    if dk_price and card_price:
        hard = get_settings().price_band_hard_ratio
        hi, lo = max(dk_price, card_price), min(dk_price, card_price)
        if lo > 0 and hi / lo > hard:
            return _no_match(cid, cname, seen, note="Different product (price far off)")
    return _no_match(cid, cname, seen, note="Listed on Google — couldn't verify page")


async def _match_competitor(cid: str, cname: str, urls: list[str],
                            ref: ProductRecord, dk_price: float | None, *,
                            known: bool = True, card_price: float | None = None,
                            thumbnail: str | None = None,
                            card_title: str | None = None) -> CompetitorMatch:
    """Verify candidate PDPs for this source through the SAME matcher used by
    /compare — brand gate → select_variant → structured match → triage — and keep
    the best. Identical for KNOWN competitors and the new Google-Shopping merchants;
    the only difference is candidate discovery:
      * known  → Google URLs PLUS the competitor's OWN site-search (we have a
        scraper), fetched with the dedicated PDP scraper.
      * new    → only the discovered URLs (organic + immersive), fetched with the
        GENERIC reader. No own-search (no scraper exists for the site).
    Everything after the fetch — select_variant, structured_match, triage, pack /
    unit normalization — is the same. If nothing verifies, fall back to the
    Shopping card price so the merchant still shows."""
    scraper_id = cid if known else "generic"
    own_ranked: list[str] = []
    if known:
        # Own site-search with CANONICAL-CORE queries (brand + line + key tokens),
        # NOT the verbose name — long titles return 0 hits on these site engines.
        # Ranked by similarity so the best few survive the _MAX_CANDIDATES cap.
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
        if not u:
            continue
        key = u.split("?", 1)[0]
        if key not in seen_urls:
            seen_urls.add(key)
            cands.append(u)
    if not cands:
        return _card_match(cid, cname, 0, card_price, thumbnail, dk_price, card_title, ref)

    async def evaluate(url: str) -> tuple[float, str, object, object] | str | None:
        pdp = await fetch_product(scraper_id, url)
        if pdp is None:
            return None          # COULDN'T READ the page (no data to judge)
        pipeline.select_variant(pdp, ref.variant_spec, dk_price, ref.name)
        rec = pipeline.record_from(pdp)
        sm = structured_match(ref, rec)
        if sm.verdict is StructuredVerdict.REJECTED:
            return _REJECTED     # READ it, logic decided it's a DIFFERENT product
        tri = triage_batch(ref.name, [rec.name])
        score = max(tri[0].score if tri else 0.0, sm.features.cosine)
        return (score, "confirmed" if sm.verdict is StructuredVerdict.CONFIRMED else "possible",
                pdp, sm)

    cands = cands[:_MAX_CANDIDATES]
    seen = len(cands)
    results = await asyncio.gather(*(evaluate(u) for u in cands))
    scored = [r for r in results if isinstance(r, tuple)]
    if not scored:
        # Nothing matched. Distinguish "we read a page and it was a DIFFERENT
        # product" (a real no-match — don't show a price) from "we couldn't read
        # any page" (genuinely unverifiable — fall back to the Shopping card).
        read_and_rejected = any(r is _REJECTED for r in results)
        if read_and_rejected:
            return _no_match(cid, cname, seen, note="Different product on this site")
        return _card_match(cid, cname, seen, card_price, thumbnail, dk_price, card_title, ref)
    # Prefer CONFIRMED over possible, then higher score. gather preserves page
    # order, and max() keeps the FIRST max on ties → earlier Google rank wins.
    best = max(scored, key=lambda r: (r[1] == "confirmed", r[0]))
    score, verdict, pdp, sm = best
    rec = pipeline.record_from(pdp)
    # PRICE BAND on the verified match too: a "possible" match whose price is
    # beyond the hard ratio off DK is a different VARIANT that merely shares the
    # brand/line words — e.g. the ₹41k "Root ZX Mini" UNIT matched to a ₹5k
    # accessory. A CONFIRMED (exact) match is exempt; only loose "possible" ones
    # are price-gated, so a real same-product price gap isn't wrongly dropped.
    if verdict != "confirmed" and dk_price and rec.price:
        hard = get_settings().price_band_hard_ratio
        hi, lo = max(dk_price, rec.price), min(dk_price, rec.price)
        if lo > 0 and hi / lo > hard:
            return _no_match(cid, cname, seen, note="Different product (price far off)")
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

    # TOP-N competitors: the merchants Google Shopping lists for this product
    # (ranked), with the 4 known always present. Each is verified through the SAME
    # matcher (_match_competitor). None = Shopping lookup FAILED → fail open to the
    # legacy fixed-4 path rather than show nothing.
    top = await serp.serp_top_competitors(name, limit=10)

    if top is None:
        cands = await serp.serp_product_candidates(name)
        comps = await asyncio.gather(*(
            _match_competitor(cid, cname, cands.get(cid) or [], ref, dk_price)
            for cid, cname in COMPETITORS))
        return CompareResult(dentalkart=DkRow(name=name), dentalkart_match=dk_match,
                             competitors=list(comps))

    async def _one(m: dict) -> CompetitorMatch:
        if not m["on_shopping"]:
            return _no_match(m["cid"], m["name"], 0, note="Not on Google Shopping")
        # Direct (immersive) url first, then the free organic candidates.
        urls: list[str] = []
        if m.get("pdp_url"):
            urls.append(m["pdp_url"])
        urls += [u for u in m.get("candidates", []) if u != m.get("pdp_url")]
        return await _match_competitor(
            m["cid"], m["name"], urls, ref, dk_price,
            known=m["known"], card_price=m.get("price"), thumbnail=m.get("thumbnail"),
            card_title=m.get("card_title"))

    comps = await asyncio.gather(*(_one(m) for m in top))
    return CompareResult(dentalkart=DkRow(name=name), dentalkart_match=dk_match,
                         competitors=list(comps))
