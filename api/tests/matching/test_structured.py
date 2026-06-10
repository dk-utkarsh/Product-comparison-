from app.matching.structured import (
    ProductRecord,
    StructuredVerdict,
    structured_match,
)


def _rec(name, **kw):
    return ProductRecord(name=name, **kw)


def test_variant_attr_mismatch_rejects():
    r = structured_match(
        _rec("GC Fuji IX GP Capsules A2", description="Shade A2"),
        _rec("GC Fuji IX GP Capsules A3", description="Shade A3"),
    )
    assert r.verdict == StructuredVerdict.REJECTED
    assert any("shade" in reason for reason in r.reasons)


def test_identical_product_confirms():
    r = structured_match(
        _rec("GC Fuji IX GP Capsules A2", unit_price=46.0),
        _rec("GC Fuji IX GP Capsules A2", unit_price=44.0),
    )
    assert r.verdict == StructuredVerdict.CONFIRMED


def test_category_gate_rejects():
    r = structured_match(
        _rec("Extraction forceps lower molar"),
        _rec("Diamond bur FG round"),
    )
    assert r.verdict == StructuredVerdict.REJECTED


def test_pack_difference_never_rejects_and_sets_note():
    r = structured_match(
        _rec("GC Fuji IX GP Capsules A2 Pack of 50", pack_size=50, unit_price=46.0),
        _rec("GC Fuji IX GP Capsules A2 Pack of 10", pack_size=10, unit_price=48.0),
    )
    assert r.verdict != StructuredVerdict.REJECTED
    assert r.pack_note == "50/pack vs 10/pack"


def test_unit_price_far_outside_band_is_borderline_not_confirmed():
    r = structured_match(
        _rec("Woodpecker scaler tip G1", unit_price=250.0),
        _rec("Woodpecker scaler tip G1", unit_price=22000.0),
    )
    assert r.verdict == StructuredVerdict.BORDERLINE


def test_weak_name_overlap_is_borderline():
    r = structured_match(
        _rec("Prime Dent Composite Kit", description="Light cure composite"),
        _rec("Prime Bond Adhesive", description="Bonding agent"),
    )
    assert r.verdict in (StructuredVerdict.BORDERLINE, StructuredVerdict.REJECTED)
