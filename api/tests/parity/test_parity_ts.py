"""
Parity test: compare Python /match verdict vs TS triage verdict on a
fixed corpus.

The Python pipeline is allowed to disagree on edge cases (it uses
embeddings + stricter hard gates than the TS smart-matcher). Cases where
Python is intentionally stricter are listed in PYTHON_STRICTER below
with a comment; for those, we assert Python rejects regardless of what
TS said. For everything else, we assert Python verdict is in the
TS-equivalent set.

TS verdict mapping -> Python verdict:
  accept -> confirmed | possible
  reject -> rejected
  grey   -> possible | variant | confirmed
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

FIXTURE = Path(__file__).parent / "fixtures" / "ts_verdicts.json"


# Cases where Python's stricter gates intentionally reject something TS
# called "grey" or "accept". Listed by (search, candidate) and the reason.
PYTHON_STRICTER: dict[tuple[str, str], str] = {
    ("3M Filtek Z350 XT Shade A2", "3M Filtek Z350 XT Shade A3"): "shade A2 vs A3",
    ("Endo File #25", "Endo File #15"): "ISO size 25 vs 15",
    ("Chlorhexidine 2% Mouthwash", "Chlorhexidine 5% Mouthwash"): "2% vs 5%",
    ("MBT Bracket .022 Slot", "Roth Bracket .022 Slot"): "MBT vs Roth (category exclusion)",
    ("MBT Bracket .022 Slot", "MBT Bracket .018 Slot"): ".022 vs .018 slot",
    ("3M Filtek Refill", "3M Filtek Kit"): "refill vs kit (incompatible group)",
    ("Putty Light Body", "Putty Heavy Body"): "light vs heavy body (viscosity)",
}


def _expected_python(verdict: str) -> set[str]:
    return {
        "accept": {"confirmed", "possible"},
        "reject": {"rejected"},
        "grey": {"possible", "variant", "confirmed"},
    }[verdict]


@pytest.mark.parametrize("case", json.loads(FIXTURE.read_text()))
def test_python_matches_ts(case: dict):
    client = TestClient(app)
    res = client.post(
        "/match",
        json={"search": case["search"], "candidates": [case["candidate"]]},
    )
    assert res.status_code == 200
    py_verdict = res.json()["ranked"][0]["verdict"]

    key = (case["search"], case["candidate"])
    if key in PYTHON_STRICTER:
        assert py_verdict == "rejected", (
            f"Python should reject by stricter gate ({PYTHON_STRICTER[key]}), "
            f"got {py_verdict} for {key}"
        )
        return

    expected = _expected_python(case["verdict"])
    assert py_verdict in expected, (
        f"TS said {case['verdict']} (-> {expected}), Python said {py_verdict} "
        f"for ({case['search']!r}, {case['candidate']!r})"
    )
