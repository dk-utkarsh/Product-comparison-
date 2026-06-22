"""
Regression guard for every real-world matching bug we've fixed.

Each case is a deterministic, network-free assertion on the matching primitives
(gate_check, structured_match, _pick_dk_child, select_variant). The point is that
a fixed bug STAYS fixed: if a future change re-breaks one of these, `pytest`
fails here instead of the user re-reporting it weeks later.

When you fix a new reported case, ADD it here with the product name in the test
id so the catalogue of "known-correct" behaviour keeps growing.
"""
from __future__ import annotations

import pytest

from app.matching.gates import gate_check
from app.matching.normalize import normalize_for_match as N
from app.matching.structured import ProductRecord, StructuredVerdict, structured_match
from app.matching.variant_spec import VariantSpec
from app.routes.compare import _pick_dk_child
from app.pipeline import select_variant
from app.scrapers.bridge import CompetitorProduct


# ─────────────────────────── gate_check ────────────────────────────
# (search_input, candidate, should_pass, note)
GATE_CASES = [
    # Same product under same/aliased/house brand → MUST pass.
    ("Avue AvueCal - Premixed Calcium Hydroxide Syringe",
     "AvueCal - Calcium Hydroxide Paste - 2gm Syringe", True, "avue→avuecal prefix"),
    ("Avue AvueCal - Premixed Calcium Hydroxide Syringe",
     "Dental Avenue Avuecal", True, "avue→dental avenue alias"),
    ("GC Gold Label 9 Posterior Restorative GIC",
     "GC Gold Label 9 Posterior Restorative", True, "same product"),
    # Different product / brand / type → MUST reject.
    ("Maarc Dental Tray Adeziv With Thinner", "Maarc Eazy Tray", False, "adhesive≠tray"),
    ("Sure Endo Gutta Percha Length Marked 2% #50",
     "Sure Endo Paper Points Length Marked", False, "gutta percha≠paper points"),
    ("Julldent Micro Tissue Stainless Steel Forceps - Straight Tooth",
     "Julldent Tissue Punch Drills", False, "forceps≠drills"),
    ("Maarc Articulating Paper 70 Microns Horseshoe (5533/050)",
     "Maarc Articulating Paper (Horseshoe Shape) 40µ Microns", False, "70µ≠40µ"),
    ("GC Gold Label 9", "3M Filtek Z350", False, "cross-brand control"),
]


@pytest.mark.parametrize("search,cand,expected,note", GATE_CASES, ids=[c[3] for c in GATE_CASES])
def test_gate(search, cand, expected, note):
    assert gate_check(N(search), N(cand)).passed is expected, note


# ─────────────────── structured_match (with description) ───────────────────
def test_terse_listing_confirmed_via_description():
    """Bare competitor name + identifying PDP description → strong semantic match
    (pinkblue 'Dental Avenue Avuecal' for 'Avue AvueCal …Calcium Hydroxide')."""
    inp = ProductRecord(
        name="Avue AvueCal - Premixed Calcium Hydroxide Syringe", price=100,
        description="Premixed calcium hydroxide paste in a syringe for root canal.",
    )
    cand = ProductRecord(
        name="Dental Avenue Avuecal", price=95,
        description=("Radiopaque Premixed Calcium Hydroxide Paste With Barium "
                     "Sulphate. AvueCal is syringe type for easy delivery."),
    )
    r = structured_match(inp, cand)
    assert r.verdict is not StructuredVerdict.REJECTED
    assert r.features.cosine >= 0.70, f"description boost too weak: {r.features.cosine}"


def test_near_exact_name_overrides_price_band():
    """Same product line + size but a big price gap from pack/form (a 25 Mtr reel
    vs a pack) must NOT be price-rejected — and must beat a wrong same-size
    product. The price band still vetoes WEAK-name lookalikes."""
    dk = ProductRecord(
        name="Meril Filasilk Black Braided Silk Suture Reel #2-0 (SLK20 25R) - 25Mtr",
        price=395, description="Black braided silk suture.")
    right = structured_match(
        dk, ProductRecord(name="Meril Filasilk #2-0 Black Braided Silk Suture",
                          price=609, description="Black braided silk suture."))
    wrong = structured_match(
        dk, ProductRecord(name="Meril Mericron XL #2-0 Polyester Suture",
                          price=2174, description="Polyester suture."))
    assert right.verdict is StructuredVerdict.CONFIRMED
    assert wrong.verdict is not StructuredVerdict.CONFIRMED


def test_description_boost_never_creates_cross_product_match():
    """Tray adhesive vs impression tray must stay rejected even with descriptions."""
    inp = ProductRecord(name="Maarc Dental Tray Adeziv With Thinner", price=545,
                        description="Tray adhesive with thinner for impression trays.")
    cand = ProductRecord(name="Maarc Eazy Tray", price=946,
                         description="Reusable impression tray, autoclavable.")
    assert structured_match(inp, cand).verdict is StructuredVerdict.REJECTED


# ─────────────────────────── _pick_dk_child ────────────────────────────
def _v(name, **kw):
    d = {"name": name, "price": kw.get("price", 100), "inStock": True}
    d.update(kw)
    return d


def test_same_code_children_disambiguate_by_name():
    """Two children share code (041D) → pick the one matching the input name."""
    variants = [
        _v("Julldent Micro Forcep Tooth - Angled (041D)"),
        _v("Julldent Diamond Dusted Micro Surgical Forceps - Angled 45 (041D)"),
    ]
    chosen = _pick_dk_child(
        "Julldent Diamond Dusted Micro Surgical Forceps - Angled 45 (041D)",
        "Julldent Micro Surgical Forcep – Black Series (JULL-DENT 041)", variants)
    assert chosen and "Diamond Dusted" in chosen["name"]


def test_base_input_keeps_parent_not_arbitrary_length_child():
    """Input names no length → don't drill into a length child (GBR screw)."""
    variants = [
        _v("Surgident GBR Screw ∅ 1.4mm x 3mm (SDS-140 - 030) - Pack of 5"),
        _v("Surgident GBR Screw ∅ 1.4mm x 4mm (SDS-140 - 040) - Pack of 5"),
    ]
    chosen = _pick_dk_child("Surgident GBR Screw ∅ 1.4mm (Pack of 5)",
                            "Surgident GBR Screw ∅ 1.4mm (Pack of 5)", variants)
    assert chosen is None, "should keep the base, not pick an arbitrary length"


def test_trailing_variant_code_survives_and_discriminates():
    """A bare trailing "- CODE" is the VARIANT IDENTITY, not SKU noise — the
    normalizer must keep it so adjacent children stay distinct
    ("…Green HP - SDH101G" vs "- SDH081G"). Was: both collapsed → wrong child."""
    from app.matching.normalize import normalize_for_match
    assert "SDH101G" in normalize_for_match("Labodent Diamond Stone Green HP - SDH101G")
    # the gate now separates adjacent codes…
    assert gate_check(N("Labodent Diamond Stone Green HP - SDH101G"),
                      N("Labodent Diamond Stone Green HP - SDH081G")).passed is False
    # …and child resolution lands on the exact code.
    variants = [
        _v("Labodent Diamond Stone Green HP - SDH081G"),
        _v("Labodent Diamond Stone Green HP - SDH101G"),
    ]
    chosen = _pick_dk_child(
        "Labodent Diamond Stone Green HP - SDH101G",
        "Labodent Diamond Stones HP For Zirconia & Ceramic Grinding And Polishing",
        variants)
    assert chosen and chosen["name"].strip().endswith("SDH101G")


def test_labeled_sku_tail_still_stripped():
    """A LABELED sku/code tail is still genuine noise and must be stripped."""
    from app.matching.normalize import normalize_for_match
    assert normalize_for_match("GDC Periosteal Elevator - SKU: DK12345").strip().endswith("Elevator")


def test_size_token_beats_range_child():
    """'#80' must resolve to the exact '#80', not the 'Assorted #45-80' range."""
    variants = [
        _v("Sure Endo Gutta Percha Length Marked - 2% #45-80"),
        _v("Sure Endo Gutta Percha Length Marked - 2% #80"),
    ]
    chosen = _pick_dk_child("Sure Endo Gutta Percha Length Marked - 2% #80",
                            "Sure Endo Gutta Percha Length Marked - 2%", variants)
    assert chosen and chosen["name"].endswith("#80")


# ─────────────────────────── select_variant ────────────────────────────
def test_competitor_shows_matched_subvariant_name():
    """Competitor listing → display the exact sub-variant the input names
    ('Upper 016 X 022'), not the base name."""
    cp = CompetitorProduct(
        name="Rabbit Force CIA Niti Intrusion Extrusion Wires (Pack Of 5)",
        url="x", image="", price=2804, mrp=0, discount=0, packaging="",
        in_stock=True, description="", source="pinkblue", pack_size=1, unit_price=2804,
        variants=[
            _v("Rabbit Force CIA Niti Intrusion Extrusion Wire Size 016 X 022 Short Lower", price=2804),
            _v("Rabbit Force CIA Niti Intrusion Extrusion Wire Size 016 X 022 Short Upper", price=2804),
            _v("Rabbit Force CIA Niti Intrusion Extrusion Wire Size 017 X 025 Long Upper", price=2804),
        ],
    )
    select_variant(cp, None, 2650.0, "Rabbit CIA NiTi Intrusion Archwires Upper 016 X 022 (Pack Of 5)")
    assert "016 X 022" in cp.name and "Upper" in cp.name


def test_base_input_keeps_competitor_base_name():
    """A base input (no sub-variant named) keeps the competitor base name —
    no junk like 'GC Gold Label 9 1-1 PKG'."""
    cp = CompetitorProduct(
        name="GC Gold Label 9 Posterior Restorative", url="x", image="", price=2450,
        mrp=0, discount=0, packaging="", in_stock=True, description="", source="pinkblue",
        pack_size=1, unit_price=2450,
        variants=[
            _v("GC Gold Label 9 1-1 PKG", price=2450),
            _v("GC Gold Label 9 1-2 PKG", price=2600),
        ],
    )
    select_variant(cp, None, 2760.0, "GC Gold Label 9 Posterior Restorative GIC")
    assert "PKG" not in cp.name
