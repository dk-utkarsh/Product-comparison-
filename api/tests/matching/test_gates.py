from app.matching.gates import gate_check


def test_brand_mismatch_rejects():
    r = gate_check("3M Filtek Z350", "GC Fuji IX")
    assert r.passed is False
    assert "brand" in r.reason.lower()


def test_incompatible_instrument_rejects():
    r = gate_check("Dentsply Rongeur", "Dentsply Forceps")
    assert r.passed is False
    assert "incompatible" in r.reason.lower()


def test_iso_size_mismatch_rejects():
    r = gate_check("Endo File #15", "Endo File #25")
    assert r.passed is False
    assert "iso" in r.reason.lower()


def test_shade_mismatch_rejects():
    r = gate_check("Filtek Shade A2", "Filtek Shade A3")
    assert r.passed is False


def test_concentration_mismatch_rejects():
    r = gate_check("Chlorhexidine 2%", "Chlorhexidine 5%")
    assert r.passed is False


def test_same_product_passes():
    r = gate_check("3M Filtek Z350 XT Shade A2", "3M Filtek Z350 XT A2")
    assert r.passed is True


def test_category_exclusion_monitor_vs_crown():
    r = gate_check("Monitor LCD 24 inch", "Dental Crown")
    assert r.passed is False


def test_refill_vs_kit_rejects():
    r = gate_check("3M Filtek Refill", "3M Filtek Kit")
    assert r.passed is False


def test_positioning_gauge_vs_bracket_kit_rejects():
    r = gate_check(
        "OSL Bracket Positioning Height Gauge - 0.022",
        "OSL M3 Metal Orthodontic Bracket Kits",
    )
    assert r.passed is False
    assert "category" in r.reason.lower() or "incompatible" in r.reason.lower()
