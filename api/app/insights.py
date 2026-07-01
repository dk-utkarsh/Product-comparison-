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


def _key(name: str) -> str:
    """Canonical product key — MUST match how the review endpoint stores confirmed
    matches (routes/reviews._confirm_key), else hidden/kept lookups silently miss.
    normalize_for_match keeps case, so lower()+strip() here like the store does."""
    return normalize_for_match(name).lower().strip()


def _shown_prices(competitors: list[dict], hidden_ids: set[str],
                  kept_ids: set[str]) -> list[dict]:
    """Verified competitor prices for a product, EXCLUDING hidden ones. Each entry is
    tagged `kept` when the user has confirmed it correct (label=correct) — used so a
    kept extreme-gap competitor no longer trips the mismatch 'review' flag."""
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
            out.append({"id": cid, "price": p, "url": c.get("matched_url"),
                        "kept": cid in kept_ids})
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
        seen[_key(name)] = it
    return list(seen.values())


def compute(items: list[dict], hidden: dict[str, set[str]],
            kept: dict[str, set[str]] | None = None) -> dict[str, Any]:
    """Bucket a set of results. `items` = [{name, result, run_id}] (already de-duped
    for Overall). `kept` = human-confirmed matches, so a KEEP clears the review flag.
    Returns KPIs + per-bucket product lists for the drill-downs."""
    kept = kept or {}
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
        key = _key(name)
        comps = _shown_prices(res.get("competitors", []),
                              hidden.get(key, set()), kept.get(key, set()))
        prices = [c["price"] for c in comps]
        b = _bucket(dk, prices)
        entry: dict[str, Any] = {
            "name": name, "dk": round(dk), "dk_url": dkm.get("matched_url") or "",
            "n_comp": len(prices), "competitors": comps,
            "run_id": it.get("run_id"),   # source compare → deep-link to review it
        }
        if prices:
            entry["min"] = round(min(prices))
            entry["max"] = round(max(prices))
            # Extreme gap (a competitor ≥2× off DK) is usually a MISMATCH — a
            # different/smaller product read as the same. Flag it for review (still
            # counted in the bucket), and keep it OUT of the ₹ totals so the money
            # figures aren't distorted. A KEPT competitor is human-vouched, so it no
            # longer trips the flag; hiding the wrong one drops it entirely. Either
            # way the flag clears once every extreme competitor has been reviewed.
            entry["review"] = any(
                max(dk, c["price"]) / min(dk, c["price"]) >= 2
                for c in comps if not c["kept"])
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
