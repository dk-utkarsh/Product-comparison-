"""
Two-phase per-competitor matching pipeline.

discover(): search -> name triage -> top-K PDP fetch -> structured match
            -> LLM judge for borderline -> best cell + link writes.
refresh():  re-fetch a known link's PDP for a fresh price.

All registry/DB writes are best-effort: a dead DB degrades to stateless
discovery, never to an error.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import structlog

from app import registry
from app.db import Database
from app.matching.llm_judge import JudgeBudget, JudgeVerdict, judge_pair
from app.matching.score import Verdict
from app.matching.structured import (
    ProductRecord,
    StructuredVerdict,
    structured_match,
)
from app.matching.tokens import distinguishing_tokens
from app.matching.triage import TriageResult, triage_batch
from app.scrapers.bridge import CompetitorProduct, fetch_product, scrape_competitor
from app.settings import get_settings

log = structlog.get_logger()


@dataclass(slots=True)
class Cell:
    """One competitor cell of the result row."""
    candidate: CompetitorProduct | None
    verdict: str | None          # confirmed | possible | variant | None
    confidence: float
    reasons: list[str]
    matched_by: str | None       # rules | llm | registry | None
    pack_note: str | None
    candidates_seen: int


def record_from(cp: CompetitorProduct) -> ProductRecord:
    return ProductRecord(
        name=cp.name, url=cp.url, description=cp.description,
        packaging=cp.packaging, price=cp.price, mrp=cp.mrp,
        pack_size=cp.pack_size, unit_price=cp.unit_price,
        sku=cp.sku, source=cp.source,
    )


async def scrape_all_queries(competitor_id: str, queries: list[str]) -> list[CompetitorProduct]:
    """Fire every query in parallel, pool unique candidates by URL."""
    raws = await asyncio.gather(
        *(scrape_competitor(competitor_id, q) for q in queries),
        return_exceptions=True,
    )
    seen: set[str] = set()
    pooled: list[CompetitorProduct] = []
    for r in raws:
        if not isinstance(r, list):
            continue
        for cand in r:
            key = cand.url or cand.name
            if key and key not in seen:
                seen.add(key)
                pooled.append(cand)
    return pooled


def _prefilter(search: str, candidates: list[CompetitorProduct]) -> list[CompetitorProduct]:
    sig = distinguishing_tokens(search)
    if not sig:
        return candidates
    return [c for c in candidates if c.name and distinguishing_tokens(c.name) & sig]


def _top_candidates(
    dk_name: str, pool: list[CompetitorProduct]
) -> list[tuple[CompetitorProduct, TriageResult]]:
    """Cheap name triage; keep the K most plausible for PDP fetching."""
    settings = get_settings()
    results = triage_batch(dk_name, [c.name for c in pool])
    scored = [
        (c, r) for c, r in zip(pool, results, strict=True)
        if r.verdict != Verdict.REJECTED and r.score >= settings.variant_threshold
    ]
    scored.sort(key=lambda cr: cr[1].score, reverse=True)
    return scored[: settings.pdp_top_k]


def _judge_to_cell_verdict(jv: JudgeVerdict) -> str | None:
    if jv.same_product and jv.same_variant:
        return "confirmed"
    if jv.same_product:
        return "variant"
    return None  # rejected


_VERDICT_RANK = {"confirmed": 3, "possible": 2, "variant": 1}


async def discover(
    competitor_id: str,
    queries: list[str],
    dk_record: ProductRecord,
    *,
    budget: JudgeBudget,
    db: Database | None,
    product_id: int | None,
) -> Cell:
    pooled = await scrape_all_queries(competitor_id, queries)
    pool = _prefilter(dk_record.name, [c for c in pooled if c.name and c.price > 0])

    killed: set[str] = set()
    if db is not None and product_id is not None:
        try:
            killed = await registry.get_killed_urls(db, product_id, competitor_id)
        except Exception:  # registry is best-effort
            log.warning("killed-url lookup failed", competitor=competitor_id)
    if killed:
        pool = [c for c in pool if c.url not in killed]

    if not pool:
        return Cell(None, None, 0.0, [], None, None, len(pooled))

    best: Cell | None = None
    for cand, tri in _top_candidates(dk_record.name, pool):
        pdp = await fetch_product(competitor_id, cand.url)
        rich = pdp or cand  # thin fallback: search-card data only
        if rich.url in killed:
            # PDP fetch can canonicalize the URL into a killed one.
            continue
        rec = record_from(rich)
        sm = structured_match(dk_record, rec)

        verdict: str | None = None
        matched_by: str | None = None
        confidence = 0.0
        reasons = list(sm.reasons)
        llm_response: dict[str, Any] | None = None

        if sm.verdict == StructuredVerdict.REJECTED:
            verdict, matched_by = "rejected", "rules"
        elif sm.verdict == StructuredVerdict.CONFIRMED:
            verdict, matched_by, confidence = "confirmed", "rules", tri.score
        else:  # BORDERLINE
            jv = await judge_pair(dk_record, rec, budget)
            if jv is None:
                # judge off/exhausted/down -> unresolved, unless the price
                # band already says it's a different product (valve vs the
                # whole machine). A judge that DID run overrides the band.
                ratio = sm.features.unit_price_ratio
                max_ratio = get_settings().price_band_max_ratio
                out_of_band = ratio is not None and not (
                    (1.0 / max_ratio) <= ratio <= max_ratio
                )
                if out_of_band:
                    verdict, matched_by = "rejected", "rules"
                    reasons.append("outside price band (judge unavailable)")
                else:
                    verdict, matched_by, confidence = "possible", "rules", tri.score
                    reasons.append("needs review (judge unavailable)")
            else:
                mapped = _judge_to_cell_verdict(jv)
                verdict = mapped or "rejected"
                matched_by, confidence = "llm", jv.confidence
                reasons.append(f"judge: {jv.reason}")
                llm_response = {
                    "same_product": jv.same_product, "same_variant": jv.same_variant,
                    "differences": jv.differences, "confidence": jv.confidence,
                    "reason": jv.reason,
                }

        if sm.features.thin_data and verdict == "confirmed":
            verdict = "possible"  # thin data caps confidence per spec
            reasons.append("capped: thin data")

        # Persist every decision (best-effort).
        if db is not None and product_id is not None and verdict is not None:
            try:
                await registry.upsert_competitor_product(db, rec)
                await registry.upsert_link(
                    db, product_id, competitor_id, rich.url,
                    verdict=verdict, confidence=confidence,
                    matched_by=matched_by or "rules",
                    reason="; ".join(reasons)[:500], llm_response=llm_response,
                )
            except Exception:  # registry is best-effort
                log.warning("registry write failed", competitor=competitor_id)

        if verdict in _VERDICT_RANK:
            cell = Cell(rich, verdict, confidence, reasons, matched_by,
                        sm.pack_note, len(pooled))
            if best is None or (
                (_VERDICT_RANK[verdict], confidence)
                > (_VERDICT_RANK[best.verdict or ""], best.confidence)
            ):
                best = cell

    return best or Cell(None, None, 0.0, [], None, None, len(pooled))


async def refresh(competitor_id: str, link: registry.Link) -> Cell | None:
    """Re-scrape a known link's PDP for a fresh price. None -> caller
    falls back to discovery for this competitor."""
    pdp = await fetch_product(competitor_id, link.competitor_url)
    if pdp is None or pdp.price <= 0:
        return None
    return Cell(
        candidate=pdp, verdict=link.verdict, confidence=link.confidence,
        reasons=[f"registry ({link.status})", link.reason or ""],
        matched_by="registry", pack_note=None, candidates_seen=0,
    )
