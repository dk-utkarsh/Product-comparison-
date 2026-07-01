"""
Pricing insights — deterministic analytics over stored compare results.

Buckets each product (DK vs its VERIFIED competitors) using a flat ±₹25 margin:
  • overpriced — a competitor is > ₹25 BELOW DK (someone undercuts us) → lower price
  • cheapest   — every competitor is ≥ DK (at least one > ₹25 above, none below) → we win
  • parity     — every competitor within ±₹25 of DK → matched market
  • monopoly   — no verified competitor → pricing power (no ₹ benchmark)

Competitors the user HID (confirmed no_match) are excluded. For the OVERALL view the
caller de-dups to the latest result per product first. No AI — pure math on prices.
"""
from __future__ import annotations

from typing import Any

from app.matching.normalize import normalize_for_match

MARGIN = 25.0          # ±₹25 = "same price" (flat, not %, per product owner's call)
_MIN_CONF = 0.7        # a shown/verified competitor match (mirrors the UI)


def _shown_prices(competitors: list[dict], hidden_ids: set[str]) -> list[dict]:
    """Verified competitor prices for a product, EXCLUDING hidden ones."""
    out: list[dict] = []
    for c in competitors or []:
        cid = c.get("competitor_id")
        price = c.get("matched_price")
        if not cid or cid in hidden_ids or price is None:
            continue
        if (c.get("score") or 0) < _MIN_CONF:
            continue
        p = float(price)
        if p > 0:
            out.append({"id": cid, "price": p, "url": c.get("matched_url")})
    return out


def _bucket(dk: float, prices: list[float]) -> str:
    if not prices:
        return "monopoly"
    if any(p < dk - MARGIN for p in prices):     # someone clearly cheaper
        return "overpriced"
    if any(p > dk + MARGIN for p in prices):     # nobody cheaper, someone clearly dearer
        return "cheapest"
    return "parity"                              # everyone within ±₹25


def dedup_latest(items: list[dict]) -> list[dict]:
    """Keep ONE entry per product — the most recent (items come oldest→newest, so the
    last write wins). So re-running a product with unchanged prices doesn't
    double-count, and changed prices simply replace the old snapshot."""
    seen: dict[str, dict] = {}
    for it in items:
        res = it.get("result") or {}
        name = (res.get("dentalkart") or {}).get("name") or it.get("name") or ""
        seen[normalize_for_match(name)] = it
    return list(seen.values())


def compute(items: list[dict], hidden: dict[str, set[str]]) -> dict[str, Any]:
    """Bucket a set of results. `items` = [{name, result}] (already de-duped for
    Overall). Returns KPIs + per-bucket product lists for the drill-downs."""
    buckets: dict[str, list[dict]] = {
        "overpriced": [], "cheapest": [], "parity": [], "monopoly": []}
    skipped_no_dk = 0
    for it in items:
        res = it.get("result") or {}
        name = (res.get("dentalkart") or {}).get("name") or it.get("name") or ""
        dkm = res.get("dentalkart_match") or {}
        dk = dkm.get("matched_price")
        if not dk or dk <= 0:
            skipped_no_dk += 1
            continue
        dk = float(dk)
        hidden_ids = hidden.get(normalize_for_match(name), set())
        comps = _shown_prices(res.get("competitors", []), hidden_ids)
        prices = [c["price"] for c in comps]
        b = _bucket(dk, prices)
        entry: dict[str, Any] = {
            "name": name, "dk": round(dk), "dk_url": dkm.get("matched_url") or "",
            "n_comp": len(prices), "competitors": comps,
        }
        if prices:
            entry["min"] = round(min(prices))
            entry["max"] = round(max(prices))
            # Extreme gap (a competitor ≥2× off DK) is usually a MISMATCH — a
            # different/smaller product read as the same. Flag it for review (still
            # counted in the bucket), and keep it OUT of the ₹ totals so the money
            # figures aren't distorted. Reviewing (hide the wrong one) recomputes it.
            entry["review"] = any(max(dk, p) / min(dk, p) >= 2 for p in prices)
            if b == "overpriced":
                entry["cut"] = round(dk - min(prices))       # cut this to match cheapest
            elif b == "cheapest":
                entry["headroom"] = round(min(prices) - dk)  # room to raise, stay cheapest
        buckets[b].append(entry)

    # biggest first inside each actionable bucket
    buckets["overpriced"].sort(key=lambda e: e.get("cut", 0), reverse=True)
    buckets["cheapest"].sort(key=lambda e: e.get("headroom", 0), reverse=True)
    buckets["monopoly"].sort(key=lambda e: e.get("dk", 0), reverse=True)

    analysed = sum(len(v) for v in buckets.values())
    kpis = {
        "analysed": analysed,
        "skipped_no_dk_price": skipped_no_dk,
        "overpriced": len(buckets["overpriced"]),
        "cheapest": len(buckets["cheapest"]),
        "parity": len(buckets["parity"]),
        "monopoly": len(buckets["monopoly"]),
        # products flagged as a likely mismatch (extreme price gap) across all buckets
        "flagged_review": sum(1 for v in buckets.values() for e in v if e.get("review")),
        # ₹ totals EXCLUDE flagged products so the money figures stay honest
        "undercut_exposure": round(sum(e.get("cut", 0) for e in buckets["overpriced"] if not e.get("review"))),
        "raise_headroom": round(sum(e.get("headroom", 0) for e in buckets["cheapest"] if not e.get("review"))),
        "margin": MARGIN,
    }
    return {"kpis": kpis, "buckets": buckets}
