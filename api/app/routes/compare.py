"""
End-to-end comparison routes — Python orchestrates, TS scrapers fetch.

For each Dentalkart product (uploaded via xlsx):
  1. Fan out to every competitor via the TS scraper bridge in parallel.
  2. Each competitor returns up to N candidate listings.
  3. Run every (dk_name, candidate_name) pair through Python triage.
  4. Keep the highest-scoring confirmed/possible candidate per competitor.
  5. Compute Δ vs Dentalkart price.

UI hits POST /compare for one row or POST /compare-batch for an xlsx.
"""
from __future__ import annotations

import asyncio
import io
import re

import openpyxl
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app import pipeline, registry
from app.db import Database, get_db
from app.matching.llm_judge import JudgeBudget
from app.matching.query_builder import ProductContext, extract_smart_queries
from app.matching.score import Verdict
from app.matching.normalize import normalize_for_match
from app.matching.structured import ProductRecord
from app.matching.tokens import distinguishing_tokens, fuzz_ratio
from app.matching.triage import triage_batch
from app.matching.variant_spec import (
    VariantSpec,
    base_name,
    base_quantity,
    config_from_text,
)
from app.scrapers.bridge import (
    COMPETITORS,
    CompetitorProduct,
    fetch_product,
    scrape_competitor,
)
from app.settings import get_settings

router = APIRouter(prefix="/compare", tags=["compare"])

_ACCEPT_VERDICTS = (Verdict.CONFIRMED.value, Verdict.POSSIBLE.value)


class DkRow(BaseModel):
    name: str = Field(min_length=1)


class CompetitorMatch(BaseModel):
    competitor_id: str
    competitor_name: str
    candidates_seen: int
    matched_name: str | None
    matched_url: str | None
    matched_price: float | None
    matched_image: str | None
    in_stock: bool | None
    verdict: str | None
    score: float | None
    cosine: float | None
    reasons: list[str] = []
    price_diff_vs_dk: float | None = None
    price_diff_per_unit: float | None = None
    matched_by: str | None = None
    pack_note: str | None = None
    spec_match: str | None = None  # exact | same-tier | different-size


class CompareResult(BaseModel):
    dentalkart: DkRow
    dentalkart_match: CompetitorMatch | None = None
    competitors: list[CompetitorMatch]


class CompareBatchResponse(BaseModel):
    total: int
    results: list[CompareResult]


def _prefilter_candidates(
    search: str, candidates: list[CompetitorProduct]
) -> list[CompetitorProduct]:
    """Sparse pre-filter: only keep candidates that share at least one
    distinguishing token with the search (stopwords excluded).

    Without this, broader fallback queries dump a lot of obviously-unrelated
    products into the pool and waste embedder cycles. With it, the pool
    shrinks to plausible candidates only.
    """
    sig = distinguishing_tokens(search)
    if not sig:
        return candidates
    kept: list[CompetitorProduct] = []
    for c in candidates:
        if not c.name:
            continue
        if distinguishing_tokens(c.name) & sig:
            kept.append(c)
    return kept


def _in_price_band(
    cand_price: float, dk_price: float | None, max_ratio: float
) -> bool:
    """True when the competitor price is plausibly the same product as DK.

    If we don't know the DK price (e.g. dentalkart.com had no match for
    the row), we can't check price sanity — pass everything through.
    """
    if not dk_price or dk_price <= 0 or cand_price <= 0:
        return True
    ratio = cand_price / dk_price
    return (1.0 / max_ratio) <= ratio <= max_ratio


def _best_match(
    dk_name: str, candidates: list[CompetitorProduct], dk_price: float | None
) -> CompetitorMatch | None:
    """Pre-filter, batch-triage, pick the highest-scoring confirmed/possible
    candidate that also lies within the DK price band."""
    if not candidates:
        return None

    pool = [c for c in candidates if c.name and c.price > 0]
    pool = _prefilter_candidates(dk_name, pool)
    if not pool:
        return None

    results = triage_batch(dk_name, [c.name for c in pool])
    max_ratio = get_settings().price_band_max_ratio

    best_score = -1.0
    best_cand: CompetitorProduct | None = None
    best_result = None
    for cand, r in zip(pool, results, strict=True):
        if r.verdict.value not in _ACCEPT_VERDICTS:
            continue
        if not _in_price_band(cand.price, dk_price, max_ratio):
            # Cosine + token may agree, but the price says it's a
            # different product (a part vs the machine, a kit vs a single
            # instrument, etc.). Drop it.
            continue
        if r.score > best_score:
            best_score = r.score
            best_cand = cand
            best_result = r

    if best_cand is None or best_result is None:
        return None

    diff: float | None = None
    if dk_price and dk_price > 0:
        diff = round(dk_price - best_cand.price, 2)

    return CompetitorMatch(
        competitor_id="",
        competitor_name="",
        candidates_seen=len(candidates),
        matched_name=best_cand.name,
        matched_url=best_cand.url,
        matched_price=best_cand.price,
        matched_image=best_cand.image,
        in_stock=best_cand.in_stock,
        verdict=best_result.verdict.value,
        score=best_result.score,
        cosine=best_result.cosine,
        reasons=best_result.reasons,
        price_diff_vs_dk=diff,
    )


def _empty_cell(cid: str, cname: str, seen: int) -> CompetitorMatch:
    return CompetitorMatch(
        competitor_id=cid,
        competitor_name=cname,
        candidates_seen=seen,
        matched_name=None,
        matched_url=None,
        matched_price=None,
        matched_image=None,
        in_stock=None,
        verdict=None,
        score=None,
        cosine=None,
        reasons=[],
        price_diff_vs_dk=None,
    )


def _per_unit_diff(
    dk_record: ProductRecord | None, c: CompetitorProduct,
    dk_price: float | None, dk_unit_price: float | None,
) -> float | None:
    """Per-unit Δ for an apples-to-apples comparison when the two sides differ in
    size. Prefer composition base quantity (e.g. ₹ per gram of powder); fall
    back to pack-size unit price."""
    c_spec = VariantSpec.from_dict(c.variant_spec)
    dk_spec = dk_record.variant_spec if dk_record else None
    if (
        dk_spec is not None and c_spec is not None
        and dk_price and dk_price > 0 and c.price > 0
    ):
        dk_qty, dk_unit = base_quantity(dk_spec)
        c_qty, c_unit = base_quantity(c_spec)
        if dk_unit == c_unit and dk_qty > 0 and c_qty > 0 and (dk_qty != c_qty):
            return round(dk_price / dk_qty - c.price / c_qty, 2)
    if dk_unit_price and dk_unit_price > 0 and c.unit_price > 0:
        return round(dk_unit_price - c.unit_price, 2)
    return None


def _cell_to_match(cid: str, cname: str, cell: pipeline.Cell,
                   dk_price: float | None,
                   dk_unit_price: float | None,
                   dk_record: ProductRecord | None = None) -> CompetitorMatch:
    c = cell.candidate
    if c is None or cell.verdict is None:
        return _empty_cell(cid, cname, cell.candidates_seen)
    diff = round(dk_price - c.price, 2) if dk_price and dk_price > 0 else None
    # Different size/pack makes the headline Δ misleading — also expose a
    # per-unit Δ so the UI can show an apples-to-apples comparison.
    unit_diff: float | None = None
    if cell.pack_note:
        unit_diff = _per_unit_diff(dk_record, c, dk_price, dk_unit_price)
    reasons = list(cell.reasons)
    # Show the correct product even when it's out of stock (flag it).
    if c.in_stock is False:
        reasons = ["out of stock", *reasons]
    return CompetitorMatch(
        competitor_id=cid, competitor_name=cname,
        candidates_seen=cell.candidates_seen,
        matched_name=c.name, matched_url=c.url, matched_price=c.price,
        matched_image=c.image, in_stock=c.in_stock,
        verdict=cell.verdict, score=cell.confidence, cosine=None,
        reasons=reasons, price_diff_vs_dk=diff,
        price_diff_per_unit=unit_diff,
        matched_by=cell.matched_by, pack_note=cell.pack_note,
        spec_match=cell.spec_match,
    )


def _paren_code(name: str) -> str:
    """Last parenthetical code of a product name, normalized — the per-variant
    model code that distinguishes siblings, e.g. "(KGF 8)" → "kgf 8",
    "(KO 12K P03A)" → "ko 12k p03a". (For grouped products whose children all
    share one code like "(JULL-DENT 223)" it simply doesn't narrow anything.)"""
    codes = re.findall(r"\(([^)]+)\)", name)
    if not codes:
        return ""
    return re.sub(r"\s+", " ", codes[-1]).strip().lower()


def _pick_dk_child(input_name: str, variants: list[dict]) -> dict | None:
    """For a grouped Dentalkart product, resolve the input/xlsx name to the
    specific child sub-variant: filter by config (tier/torque/Extra), then by
    exact model code, then closest name. Returns None when the input pins
    neither a config nor a model code (keep the grouped listing as-is)."""
    in_kit, in_torque, in_extra = config_from_text(input_name)
    in_code = _paren_code(input_name)
    # The shared parent code (e.g. "JULL-DENT 223") isn't a per-variant
    # discriminator; treat a code as useful only if it differs across children.
    distinct_codes = {_paren_code(str(v.get("name", ""))) for v in variants}
    use_code = bool(in_code) and len(distinct_codes - {""}) > 1
    if not (in_kit or in_torque or in_extra or use_code):
        return None

    in_norm = normalize_for_match(input_name)
    compatible: list[dict] = []
    for v in variants:
        vs = VariantSpec.from_dict(v.get("variantSpec"))
        vk = vs.kit_tier if vs else None
        vt = vs.torque if vs else None
        ve = vs.is_extra if vs else False
        if in_kit and vk and vk != in_kit:
            continue
        if in_torque and vt and vt != in_torque:
            continue
        if in_extra != ve and (in_extra or ve):
            continue
        compatible.append(v)
    if not compatible:
        return None

    # Exact model-code match wins outright (KGF 8 vs KGF 9 vs KO 1/2).
    if use_code:
        coded = [v for v in compatible if _paren_code(str(v.get("name", ""))) == in_code]
        if coded:
            compatible = coded

    return max(
        compatible,
        key=lambda v: fuzz_ratio(in_norm, normalize_for_match(str(v.get("name", "")))),
    )


async def _resolve_dk(row: DkRow) -> tuple[CompetitorMatch | None, ProductRecord | None]:
    """Search dentalkart.com, pick the best self-match, enrich via PDP."""
    dk_raw = await scrape_competitor("dentalkart", row.name)
    # A config-specific child name (e.g. "...Premium...Torque Ratchet") may not
    # surface the grouped parent, which DK indexes under the base name. Search
    # the base name too and pool, so the parent is in the running.
    base = base_name(row.name)
    rank_name = row.name
    if base and base.lower() != row.name.lower():
        extra = await scrape_competitor("dentalkart", base)
        seen_urls = {c.url for c in dk_raw}
        dk_raw = dk_raw + [c for c in extra if c.url not in seen_urls]
        rank_name = base  # rank candidates against the base so the parent wins
    dk_match = _best_match(rank_name, dk_raw, None)
    if dk_match is None:
        return None, None
    dk_match.competitor_id = "dentalkart"
    dk_match.competitor_name = "Dentalkart"
    pdp = await fetch_product("dentalkart", dk_match.matched_url or "")
    search_cand = next((c for c in dk_raw if c.url == dk_match.matched_url), None)
    src = pdp or search_cand
    if src is None:
        return dk_match, None
    dk_record = pipeline.record_from(src)

    # Grouped product → resolve to the exact child the input names, showing that
    # child's full name + its real price + stock (the parent listing collapses
    # every variant to one wrong price, e.g. Julldent → ₹1995 'Box Only').
    child = _pick_dk_child(row.name, pdp.variants) if pdp and pdp.variants else None
    if child is not None:
        dk_match.matched_name = str(child.get("name") or dk_match.matched_name)
        dk_match.matched_price = float(child.get("price") or dk_match.matched_price or 0)
        if "inStock" in child:
            dk_match.in_stock = bool(child["inStock"])
        dk_record.name = dk_match.matched_name
        dk_record.price = dk_match.matched_price
        dk_record.pack_size = int(child.get("packSize") or 1)
        dk_record.unit_price = float(child.get("unitPrice") or dk_record.price)
        dk_record.variant_spec = VariantSpec.from_dict(child.get("variantSpec"))
        return dk_match, dk_record

    # Dentalkart is the source of truth. Use the listing price and the grouped
    # parent's composition spec from the search API — the PDP often resolves to
    # a single cheaper child (e.g. GC Gold Label 9 PDP = ₹1369 / 5g, but the
    # listing is ₹2760 / 15g+13.1g).
    if dk_match.matched_price:
        dk_record.price = dk_match.matched_price
        if dk_record.pack_size and dk_record.pack_size > 1:
            dk_record.unit_price = round(dk_record.price / dk_record.pack_size, 2)
        else:
            dk_record.unit_price = dk_record.price
    if search_cand is not None and search_cand.variant_spec:
        dk_record.variant_spec = VariantSpec.from_dict(search_cand.variant_spec)
    return dk_match, dk_record


async def _compare_one(
    row: DkRow, db: Database | None, budget: JudgeBudget
) -> CompareResult:
    dk_match, dk_record = await _resolve_dk(row)
    if dk_match is None or dk_record is None:
        # Not on dentalkart.com — report empty cells, don't guess.
        return CompareResult(
            dentalkart=row, dentalkart_match=None,
            competitors=[_empty_cell(cid, cname, 0) for cid, cname in COMPETITORS],
        )

    product_id: int | None = None
    if db is not None:
        try:
            product_id = await registry.upsert_product(db, dk_record)
        except Exception:  # registry is best-effort
            product_id = None

    ctx = ProductContext(
        description=dk_record.description or None,
        packaging=dk_record.packaging or None,
        sku=dk_record.sku,
    )
    queries = extract_smart_queries(dk_record.name, ctx) or [row.name]
    dk_price = dk_match.matched_price
    dk_unit_price = dk_record.unit_price or dk_record.price

    async def one_competitor(cid: str, cname: str) -> CompetitorMatch:
        # Phase 2: registry hit -> cheap refresh.
        if db is not None and product_id is not None:
            try:
                links = await registry.get_active_links(db, product_id, cid)
            except Exception:  # registry is best-effort
                links = []
            # Refresh only settled links. A 'possible' (judge off/over
            # budget) must go through discovery again so it can be
            # re-judged instead of being frozen forever.
            if links and (
                links[0].status == "human_verified"
                or links[0].verdict in ("confirmed", "variant")
            ):
                cell = await pipeline.refresh(cid, links[0], dk_record)
                if cell is not None:
                    return _cell_to_match(cid, cname, cell, dk_price, dk_unit_price, dk_record)
        # Phase 1: full discovery.
        cell = await pipeline.discover(
            cid, queries, dk_record,
            budget=budget, db=db, product_id=product_id, dk_price=dk_price,
        )
        return _cell_to_match(cid, cname, cell, dk_price, dk_unit_price, dk_record)

    out = list(await asyncio.gather(
        *(one_competitor(cid, cname) for cid, cname in COMPETITORS)
    ))
    return CompareResult(dentalkart=row, dentalkart_match=dk_match, competitors=out)


@router.post("/single", response_model=CompareResult)
async def compare_single(row: DkRow) -> CompareResult:
    db: Database | None
    try:
        db = await get_db()
    except Exception:  # run stateless without a DB
        db = None
    try:
        budget = JudgeBudget(get_settings().llm_judge_budget_per_run)
        return await _compare_one(row, db, budget)
    finally:
        if db is not None:
            await db.close()


_NAME_HEADERS = {"product name", "name", "product", "title"}


def _parse_dk_xlsx(content: bytes) -> list[DkRow]:
    """Pull just the product-name column out of the xlsx. Everything else
    (SKU, brand, price) is ignored — we derive what we need from the live
    dentalkart.com scrape."""
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    if ws is None:
        return []
    iter_rows = ws.iter_rows(values_only=True)
    try:
        header = next(iter_rows)
    except StopIteration:
        return []

    headers_lc = [str(h).strip().lower() if h is not None else "" for h in header]

    n: int | None = None
    for i, h in enumerate(headers_lc):
        if h in _NAME_HEADERS:
            n = i
            break
    if n is None:
        if len(headers_lc) == 1:
            n = 0
        else:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"no product-name column found. Looked for one of "
                    f"{sorted(_NAME_HEADERS)}; got headers {headers_lc}"
                ),
            )

    rows: list[DkRow] = []
    for r in iter_rows:
        if not r or n >= len(r) or r[n] is None:
            continue
        name = str(r[n]).strip()
        if name:
            rows.append(DkRow(name=name))
    return rows


@router.post("/batch", response_model=CompareBatchResponse)
async def compare_batch(
    file: UploadFile = File(...),
    concurrency: int = 2,
) -> CompareBatchResponse:
    """Run /compare/single for every row of the xlsx.

    Concurrency is bounded — each row already fans out across the configured
    competitors,
    so running too many rows in parallel will trip rate limits on competitor
    sites. Default 2 in-flight rows.
    """
    content = await file.read()
    rows = _parse_dk_xlsx(content)
    if not rows:
        raise HTTPException(status_code=400, detail="no usable rows in xlsx")

    db: Database | None
    try:
        db = await get_db()
    except Exception:  # run stateless without a DB
        db = None
    budget = JudgeBudget(get_settings().llm_judge_budget_per_run)
    sem = asyncio.Semaphore(max(1, concurrency))

    async def gated(r: DkRow) -> CompareResult:
        async with sem:
            return await _compare_one(r, db, budget)

    try:
        results = await asyncio.gather(*(gated(r) for r in rows))
    finally:
        if db is not None:
            await db.close()
    return CompareBatchResponse(total=len(results), results=results)
