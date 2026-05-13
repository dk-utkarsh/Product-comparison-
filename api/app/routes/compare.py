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
from typing import Any

import openpyxl
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.matching.score import Verdict
from app.matching.triage import triage
from app.scrapers.bridge import COMPETITORS, scrape_competitor

router = APIRouter(prefix="/compare", tags=["compare"])

_ACCEPT_VERDICTS = (Verdict.CONFIRMED.value, Verdict.POSSIBLE.value)


class DkRow(BaseModel):
    name: str = Field(min_length=1)
    sku: str | None = None
    brand: str | None = None
    price: float | None = None


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


class CompareResult(BaseModel):
    dentalkart: DkRow
    competitors: list[CompetitorMatch]


class CompareBatchResponse(BaseModel):
    total: int
    results: list[CompareResult]


def _best_match(dk_name: str, candidates: list, dk_price: float | None) -> CompetitorMatch | None:
    """Run Python triage on every candidate and pick the best."""
    if not candidates:
        return None

    best_score = -1.0
    best: dict[str, Any] | None = None
    for cand in candidates:
        if not cand.name or cand.price <= 0:
            continue
        r = triage(dk_name, cand.name)
        if r.verdict.value not in _ACCEPT_VERDICTS:
            continue
        if r.score > best_score:
            best_score = r.score
            best = {
                "name": cand.name,
                "url": cand.url,
                "price": cand.price,
                "image": cand.image,
                "in_stock": cand.in_stock,
                "verdict": r.verdict.value,
                "score": r.score,
                "cosine": r.cosine,
                "reasons": r.reasons,
            }

    if best is None:
        return None

    diff: float | None = None
    if dk_price and dk_price > 0:
        diff = round(dk_price - best["price"], 2)

    return CompetitorMatch(
        competitor_id="",  # caller fills
        competitor_name="",  # caller fills
        candidates_seen=len(candidates),
        matched_name=best["name"],
        matched_url=best["url"],
        matched_price=best["price"],
        matched_image=best["image"],
        in_stock=best["in_stock"],
        verdict=best["verdict"],
        score=best["score"],
        cosine=best["cosine"],
        reasons=best["reasons"],
        price_diff_vs_dk=diff,
    )


async def _compare_one(row: DkRow) -> CompareResult:
    """Fan out across competitors for one Dentalkart row."""
    tasks = [scrape_competitor(cid, row.name) for cid, _ in COMPETITORS]
    raw_per_comp = await asyncio.gather(*tasks, return_exceptions=True)

    out: list[CompetitorMatch] = []
    for (cid, cname), result in zip(COMPETITORS, raw_per_comp, strict=True):
        candidates = result if isinstance(result, list) else []
        best = _best_match(row.name, candidates, row.price)
        if best is None:
            out.append(
                CompetitorMatch(
                    competitor_id=cid,
                    competitor_name=cname,
                    candidates_seen=len(candidates),
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
            )
        else:
            best.competitor_id = cid
            best.competitor_name = cname
            out.append(best)

    return CompareResult(dentalkart=row, competitors=out)


@router.post("/single", response_model=CompareResult)
async def compare_single(row: DkRow) -> CompareResult:
    return await _compare_one(row)


_NAME_HEADERS = {"product name", "name", "product", "title"}
_SKU_HEADERS = {"sku", "code", "item code", "product code"}
_BRAND_HEADERS = {"brand", "manufacturer"}
_PRICE_HEADERS = {"price", "mrp", "dk price"}


def _parse_dk_xlsx(content: bytes) -> list[DkRow]:
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

    def find(targets: set[str]) -> int | None:
        for i, h in enumerate(headers_lc):
            if h in targets:
                return i
        return None

    n = find(_NAME_HEADERS)
    if n is None:
        if len(headers_lc) == 1:
            n = 0
        else:
            raise HTTPException(
                status_code=400,
                detail=f"no name column. headers: {headers_lc}",
            )
    s, b, p = find(_SKU_HEADERS), find(_BRAND_HEADERS), find(_PRICE_HEADERS)

    rows: list[DkRow] = []
    for r in iter_rows:
        if not r or n >= len(r) or r[n] is None:
            continue
        name = str(r[n]).strip()
        if not name:
            continue
        try:
            price = float(r[p]) if p is not None and p < len(r) and r[p] is not None else None
        except (TypeError, ValueError):
            price = None
        rows.append(
            DkRow(
                name=name,
                sku=str(r[s]).strip() if s is not None and s < len(r) and r[s] else None,
                brand=str(r[b]).strip() if b is not None and b < len(r) and r[b] else None,
                price=price,
            )
        )
    return rows


@router.post("/batch", response_model=CompareBatchResponse)
async def compare_batch(
    file: UploadFile = File(...),
    concurrency: int = 2,
) -> CompareBatchResponse:
    """Run /compare/single for every row of the xlsx.

    Concurrency is bounded — each row already fans out across 9 competitors,
    so running too many rows in parallel will trip rate limits on competitor
    sites. Default 2 in-flight rows.
    """
    content = await file.read()
    rows = _parse_dk_xlsx(content)
    if not rows:
        raise HTTPException(status_code=400, detail="no usable rows in xlsx")

    sem = asyncio.Semaphore(max(1, concurrency))

    async def gated(r: DkRow) -> CompareResult:
        async with sem:
            return await _compare_one(r)

    results = await asyncio.gather(*(gated(r) for r in rows))
    return CompareBatchResponse(total=len(results), results=results)
