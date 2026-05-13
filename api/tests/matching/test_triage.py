from app.matching.score import Verdict
from app.matching.triage import triage


def test_brand_mismatch_short_circuits_to_rejected():
    r = triage("3M Filtek Z350 XT", "GC Fuji IX")
    assert r.verdict == Verdict.REJECTED
    assert "brand" in r.reasons[0].lower()


def test_same_product_confirmed():
    r = triage("3M Filtek Z350 XT Shade A2", "3M Filtek Z350 XT A2")
    assert r.verdict == Verdict.CONFIRMED


def test_iso_conflict_rejected():
    r = triage("Dentsply Endo File #15", "Dentsply Endo File #25")
    assert r.verdict == Verdict.REJECTED
