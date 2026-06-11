"""pipeline.discover() price-band behaviour in rules-only mode (judge unavailable).

A BORDERLINE pair whose unit price sits far outside the band (a ₹450 valve
vs a ₹22,512 machine) must come back rejected, not as a visible 'possible'.
"""
import asyncio

from app import pipeline
from app.matching.llm_judge import JudgeBudget
from app.matching.structured import ProductRecord
from app.scrapers.bridge import CompetitorProduct


def _cp(name, url, price):
    return CompetitorProduct(
        name=name, url=url, image="", price=price, mrp=price, discount=0,
        packaging="", in_stock=True, description="",
        source="pinkblue", pack_size=1, unit_price=price, sku=None,
    )


def _dk(price):
    return ProductRecord(
        name="Bestodent Air Compressor", url="https://www.dentalkart.com/compressor.html",
        description="Oil-free dental air compressor", price=price, mrp=price,
        pack_size=1, unit_price=price, source="dentalkart",
    )


def _discover(monkeypatch, cand):
    async def fake_search(cid, query):
        return [cand]

    async def fake_pdp(cid, url):
        return None  # thin fallback: search-card data only

    monkeypatch.setattr(pipeline, "scrape_competitor", fake_search)
    monkeypatch.setattr(pipeline, "fetch_product", fake_pdp)
    return asyncio.run(pipeline.discover(
        "pinkblue", ["bestodent air compressor"], _dk(22512),
        budget=JudgeBudget(0),  # judge unavailable
        db=None, product_id=None,
    ))


def test_out_of_band_borderline_is_rejected_when_judge_unavailable(monkeypatch):
    cand = _cp("Bestodent Air Compressor Valve",
               "https://pinkblue.in/compressor-valve", 450)  # ~50x off
    cell = _discover(monkeypatch, cand)
    assert cell.verdict is None
    assert cell.candidate is None


def test_in_band_borderline_stays_possible_when_judge_unavailable(monkeypatch):
    cand = _cp("Bestodent Air Compressor",
               "https://pinkblue.in/compressor", 21900)
    cell = _discover(monkeypatch, cand)
    assert cell.verdict == "possible"
    assert cell.candidate is not None
