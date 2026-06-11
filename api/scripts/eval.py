"""
Pipeline evaluation against golden_links.

For every labeled (dk_name, source) pair, run the live compare pipeline and
check the predicted URL against the golden truth. Reports per-source and
overall precision/recall.

  correct + predicted same URL        -> true positive
  correct + predicted other/none      -> false negative (+FP if other URL)
  no_match + predicted none           -> true negative
  no_match + predicted any URL        -> false positive

Usage:  cd api && uv run python scripts/eval.py
Needs:  Postgres + the Node sidecar running (live scrapes).
"""
from __future__ import annotations

import asyncio
from collections import defaultdict

from app.db import get_db
from app.matching.llm_judge import JudgeBudget
from app.routes.compare import DkRow, _compare_one
from app.settings import get_settings


def _norm_url(u: str | None) -> str:
    return (u or "").split("?")[0].rstrip("/").lower()


async def main() -> None:
    db = await get_db()
    try:
        rows = await db.fetch(
            "SELECT dk_name, source, competitor_url, label FROM golden_links "
            "ORDER BY dk_name"
        )
        if not rows:
            print("golden_links is empty — label some rows in the UI first (⭐ / ∅).")
            return

        golden: dict[str, list] = defaultdict(list)
        for r in rows:
            golden[r["dk_name"]].append(r)

        stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        budget = JudgeBudget(get_settings().llm_judge_budget_per_run * 10)

        for i, (dk_name, labels) in enumerate(golden.items(), 1):
            print(f"[{i}/{len(golden)}] {dk_name}")
            result = await _compare_one(DkRow(name=dk_name), db, budget)
            predicted = {
                c.competitor_id: _norm_url(c.matched_url)
                for c in result.competitors
                if c.verdict in ("confirmed", "possible")
            }
            for g in labels:
                src, s = g["source"], stats[g["source"]]
                pred = predicted.get(src, "")
                truth = _norm_url(g["competitor_url"])
                if g["label"] == "correct":
                    if pred and pred == truth:
                        s["tp"] += 1
                    else:
                        s["fn"] += 1
                        if pred:
                            s["fp"] += 1
                else:  # no_match
                    if pred:
                        s["fp"] += 1
                    else:
                        s["tn"] += 1

        print("\n== results ==")
        totals: dict[str, int] = defaultdict(int)
        for src, s in sorted(stats.items()):
            for k, v in s.items():
                totals[k] += v
            p = s["tp"] / (s["tp"] + s["fp"]) if s["tp"] + s["fp"] else 0.0
            r = s["tp"] / (s["tp"] + s["fn"]) if s["tp"] + s["fn"] else 0.0
            print(f"{src:12s} tp={s['tp']:3d} fp={s['fp']:3d} fn={s['fn']:3d} "
                  f"tn={s['tn']:3d}  precision={p:.2f} recall={r:.2f}")
        p = totals["tp"] / (totals["tp"] + totals["fp"]) if totals["tp"] + totals["fp"] else 0.0
        r = totals["tp"] / (totals["tp"] + totals["fn"]) if totals["tp"] + totals["fn"] else 0.0
        print(f"{'OVERALL':12s} precision={p:.2f} recall={r:.2f}")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
