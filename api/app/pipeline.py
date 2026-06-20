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
from app.matching.normalize import normalize_for_match
from app.matching.tokens import distinguishing_tokens
from app.matching.triage import TriageResult, triage_batch
from app.matching.variant_spec import SpecMatch, VariantSpec, has_size_signal
from app.matching.variant_spec import compare as compare_spec
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
    spec_match: str | None = None  # exact | same-tier | different-size | None


def record_from(cp: CompetitorProduct) -> ProductRecord:
    return ProductRecord(
        name=cp.name, url=cp.url, description=cp.description,
        packaging=cp.packaging, price=cp.price, mrp=cp.mrp,
        pack_size=cp.pack_size, unit_price=cp.unit_price,
        sku=cp.sku, source=cp.source,
        variant_spec=VariantSpec.from_dict(cp.variant_spec),
    )


# Rank for choosing among a listing's sub-variants vs the DK truth spec.
_SPEC_RANK = {
    SpecMatch.EXACT: 0,
    SpecMatch.SAME_TIER: 1,
    SpecMatch.UNKNOWN: 2,
    SpecMatch.DIFFERENT_SIZE: 3,
}


def select_variant(
    cp: CompetitorProduct, dk_spec: VariantSpec | None, dk_price: float | None
) -> None:
    """When a competitor listing has sub-variants, pick the one matching the
    Dentalkart product and rewrite `cp`'s price/spec to it. The "Extra"
    formulation line is never selected against a non-Extra DK product (and vice
    versa). Selection order: spec match (exact > same-tier > unknown >
    different-size), then price-proximity to the DK listing price — which also
    disambiguates grams-less variants (Big ₹2580 ≈ DK ₹2760, not Mini ₹1286).
    """
    # Real variants only — drop Shopify's placeholder "Default Title" (a
    # single-variant product) and empty rows.
    real = [
        v for v in cp.variants
        if str(v.get("name") or "").strip().lower() not in ("", "default title")
        and float(v.get("price") or 0) > 0
    ]
    if len(real) < 2:
        return  # nothing meaningful to choose between

    # Only intervene for genuine SIZE/composition variants. Shade/slot variants
    # (no size signal) must not be reshuffled by price — that just swaps the
    # listing price for an arbitrary same-product variant.
    dk_has = dk_spec is not None and has_size_signal(dk_spec)
    variant_specs = [VariantSpec.from_dict(v.get("variantSpec")) for v in real]
    any_var_has = any(vs is not None and has_size_signal(vs) for vs in variant_specs)
    if not (dk_has or any_var_has):
        return

    scored: list[tuple[int, float, dict[str, Any]]] = []
    for v, vs in zip(real, variant_specs, strict=True):
        match = (
            compare_spec(dk_spec, vs)
            if dk_spec is not None and vs is not None
            else SpecMatch.UNKNOWN
        )
        if match is SpecMatch.DIFFERENT_FORMULATION:
            continue  # never cross the Extra / non-Extra line
        price = float(v.get("price") or 0)
        prox = abs(price - dk_price) if dk_price and dk_price > 0 else 0.0
        scored.append((_SPEC_RANK.get(match, 2), prox, v))

    if not scored:
        return
    scored.sort(key=lambda t: (t[0], t[1]))
    _, _, chosen = scored[0]

    # Rewrite price/spec only — leave cp.name untouched so name-similarity
    # scoring isn't polluted by appended variant labels.
    cp.price = float(chosen.get("price") or cp.price)
    cp.mrp = float(chosen.get("mrp") or cp.mrp)
    cp.pack_size = int(chosen.get("packSize") or cp.pack_size)
    cp.unit_price = float(chosen.get("unitPrice") or cp.unit_price)
    if chosen.get("variantSpec"):
        cp.variant_spec = chosen["variantSpec"]


def _canonical_key(cand: CompetitorProduct) -> str:
    """Dedup key for a candidate. Strip the URL query string (and trailing
    slash) so the SAME product returned by different queries — e.g. oralkart's
    search-tracking params '?_pos=2&_psq=…' — collapses to one entry instead of
    eating several PDP-fetch slots. Falls back to the normalized name."""
    url = cand.url or ""
    base = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    return base or normalize_for_match(cand.name or "")


async def scrape_all_queries(competitor_id: str, queries: list[str]) -> list[CompetitorProduct]:
    """Fire every query in parallel, pool unique candidates by canonical URL."""
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
            key = _canonical_key(cand)
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
    dk_price: float | None = None,
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

    top = _top_candidates(dk_record.name, pool)
    # Fetch every top-K PDP concurrently. The per-candidate work below is CPU +
    # (optional) judge only, so overlapping these often-slow network calls cuts
    # latency without changing which candidates are evaluated, their order, or
    # the judge budget consumption order.
    pdps = await asyncio.gather(
        *(fetch_product(competitor_id, cand.url) for cand, _ in top),
        return_exceptions=True,
    )

    best: Cell | None = None
    for (cand, tri), pdp in zip(top, pdps, strict=True):
        if isinstance(pdp, BaseException):
            pdp = None
        rich = pdp or cand  # thin fallback: search-card data only
        if rich.url in killed:
            # PDP fetch can canonicalize the URL into a killed one.
            continue
        # Configurable/grouped listing → pick the sub-variant matching DK.
        select_variant(rich, dk_record.variant_spec, dk_price)
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
                        sm.pack_note, len(pooled), spec_match=sm.spec_match)
            if best is None or (
                (_VERDICT_RANK[verdict], confidence)
                > (_VERDICT_RANK[best.verdict or ""], best.confidence)
            ):
                best = cell

    return best or Cell(None, None, 0.0, [], None, None, len(pooled))


async def refresh(
    competitor_id: str, link: registry.Link, dk_record: ProductRecord
) -> Cell | None:
    """Re-scrape a known link's PDP for a fresh price. None -> caller
    falls back to discovery for this competitor."""
    pdp = await fetch_product(competitor_id, link.competitor_url)
    if pdp is None or pdp.price <= 0:
        return None
    pack_note: str | None = None
    if (pdp.pack_size != dk_record.pack_size
            and pdp.pack_size > 0 and dk_record.pack_size > 0):
        pack_note = f"{dk_record.pack_size}/pack vs {pdp.pack_size}/pack"
    return Cell(
        candidate=pdp, verdict=link.verdict, confidence=link.confidence,
        reasons=[f"registry ({link.status})", link.reason or ""],
        matched_by="registry", pack_note=pack_note, candidates_seen=0,
    )
