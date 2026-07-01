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
    # A shared GENERIC instrument TYPE (gracey/curette/scaler…) is NOT a brand match:
    # DK brand "Julldent" absent from name AND description → reject, even though both
    # say "Gracey Curette". (Distinctive lines like Ketac/Fuji still pass — below.)
    ("Julldent Anterior Gracey Curette - SGCC 1/2", "Gracey Curette #1/2 Rigid",
     False, "generic type 'gracey' shared ≠ brand (Julldent absent)"),
    ("Julldent Anterior Gracey Curette - SGCC 1/2", "GDC Gracey Curette 1/2",
     False, "different brand GDC, only generic 'gracey' shared"),
    ("Julldent Anterior Gracey Curette - SGCC 1/2", "Julldent Gracey Curette SGCC 1/2",
     True, "same brand Julldent present"),
    # Distinctive coined product LINE substitutes for the dropped manufacturer.
    ("3M ESPE Ketac Molar Glass Ionomer", "Ketac Molar", True, "distinctive line Ketac"),
    ("GC Fuji IX GP Glass Ionomer", "Fuji IX GP", True, "distinctive line Fuji"),
    # Same brand + family, DIFFERENT product LINE → reject. Shares "3m espe ketac" +
    # generic "glass ionomer restorative", but molar ≠ universal is the real product
    # (Ketac Molar posterior restorative vs Ketac Universal). Pack also differs
    # (15g vs 12.5g), but the line word alone must reject.
    ("3M ESPE Ketac Molar Glass Ionomer Restorative Cement - Shade A3 (15g Powder)",
     "3m Espe Ketac Universal Glass Ionomer Restorative", False, "Ketac Molar ≠ Ketac Universal"),
    # …but a mere descriptor difference after the SAME line still matches.
    ("3M ESPE Ketac Molar Glass Ionomer Restorative", "3M ESPE Ketac Molar GI Filling Cement",
     True, "same line Ketac Molar, restorative≈filling descriptor"),
    # CONTENT amount is part of identity: a 15g pack ≠ a 12.5g pack (same line). Per
    # unit, dominant amount, ~12% tolerance so 7.8 vs 8.5 ml is still the same.
    ("GC Fuji IX GP 15g Powder 8mL Liquid", "GC Fuji IX GP 12.5g Powder 8mL Liquid",
     False, "15g ≠ 12.5g powder content"),
    ("GC Fuji IX GP 15g Powder 7.8mL Liquid", "GC Fuji IX GP 15 g Powder 8.5 mL Liquid",
     True, "same 15g; 7.8 vs 8.5 mL within tolerance"),
    # Brand spelled with/without spaces/hyphens is the SAME brand…
    ("Oracraft Tissue Plier - TP37", "Ora Craft Tissue Plier Angular 11.5cm TP37",
     True, "Oracraft == Ora Craft (spacing)"),
    ("Oracraft Warwick James Left (EWJL)", "Ora-Craft Warwick James Standard Root Elevator Left EWJL",
     True, "Oracraft == Ora-Craft (hyphen)"),
    # …but a genuinely different brand stays rejected even with a shared SKU code.
    ("Oracraft Scalpel Handle With Scale #3 (10-130-03E)",
     "GDC Scalpel Handle With Scale - No. 3 (10-130-03e)", False, "Oracraft != GDC"),
    # Single-digit SKU tails (EXS6/POW6/PCP11) discriminate near-identical probes:
    # the WHO screening probe (PCP11.5B) is NOT the plain or thin-william probe.
    ("Oracraft Single Ended WHO Screening Probe #3 - PCP11.5B",
     "Oracraft Single Ended Probe #3 - EXS6", False, "PCP11.5B != EXS6 (different probe)"),
    ("Oracraft Single Ended WHO Screening Probe #3 - PCP11.5B",
     "Oracraft Single Ended Thin Willam Probe #3 - POW6", False, "PCP11.5B != POW6"),
    # …yet a competitor that drops the code but keeps the distinctive words still
    # matches (codes are one-sided → gate must not fire).
    ("Oracraft Single Ended WHO Screening Probe #3 - PCP11.5B",
     "Ora Craft Screening Single End (WHO Probe)", True, "WHO probe, code dropped"),
    # Colour is a hard variant discriminator: same item, different colour = a
    # different product. Disjoint colours reject; a shared/absent colour does not.
    ("Kalabhai Ultra Rock Die (Brown) Stone (3kg)",
     "Kalabhai Ultra Rock Die (Yellow) Stone (3kg)", False, "Brown != Yellow"),
    ("Kalabhai Ultra Rock Die (Brown) Stone (3kg)",
     "Kalabhai Ultra Rock Die (Brown) Stone (3kg)", True, "Brown == Brown"),
    ("Kalabhai Ultra Rock Die (Brown) Stone (3kg)",
     "Kalabhai Ultrarock Die Stone 3 Kg", True, "colour one-sided → no gate"),
    # Ortho elastics: intraoral≠extraoral, and the fraction-inch size (5/8 vs 3/8)
    # + the ounce force (3.5oz vs 8oz) are hard size discriminators. Each alone
    # must split these — they're otherwise ~94% name-similar.
    ("Penta Ortho Intraoral Elastics - size 5/8 -3.5 Oz",
     "Penta Ortho Extraoral Elastics - size 3/8-8 Oz", False, "intraoral≠extraoral"),
    ("Penta Ortho Intraoral Elastics - size 5/8 -3.5 Oz",
     "Penta Ortho Intraoral Elastics - size 3/8 -8 Oz", False, "5/8 3.5oz ≠ 3/8 8oz"),
    ("Penta Ortho Intraoral Elastics - size 5/8 -3.5 Oz",
     "Penta Ortho Intraoral Elastics - size 5/8 -3.5 Oz", True, "identical → match"),
    # Cement function: a restorative (Ketac Molar) ≠ a luting cement (Ketac Cem),
    # even though both are "glass ionomer cement".
    ("3M ESPE Ketac Molar Glass Ionomer Restorative Cement",
     "3M ESPE Ketac Cem Glass Ionomer Luting Cement", False, "restorative≠luting"),
    ("3M ESPE Ketac Molar Glass Ionomer Restorative Cement",
     "3M ESPE Ketac Molar GI Filling Cement", True, "restorative vs filling (same fn)"),
    # A brand mentioned only as a COMPATIBILITY note ("… For E2ZZ, J-Morita") is
    # not the product's brand — that's a third-party part that FITS J-Morita.
    ("J Morita ZX Apex Locator Accessories",
     "Dental Apex Locator Main Cable For E2ZZ, J-Morita", False, "compatible-with ≠ brand"),
    ("Dentsply Protaper Next Files",
     "Generic Rotary Files Compatible With Dentsply Protaper", False, "compatible-with ≠ brand"),
    # Standalone decimal size (articulator inches): 3.5 ≠ 4.5.
    ('Disk Type Mean Value Dental Articulator 3.5',
     'Indian articulator disk Type Mean Value 4.5"', False, "articulator 3.5 ≠ 4.5"),
    ('Disk Type Mean Value Dental Articulator 3.5',
     'Disk Type Mean Value Dental Articulator 3.5 inch', True, "3.5 == 3.5 inch"),
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


def test_container_is_not_the_kit_that_holds_it():
    """A storage BOX is not the multi-item KIT that contains it, even when the
    names are near-identical ('Zygo Box' vs 'Zygo kit'). The product kind
    (container vs bundle) plus a 10x per-unit price gap give it away — a high
    token/fuzz score must not override that."""
    box = ProductRecord(name="Julldent Zygo Box", price=2399)
    kit = ProductRecord(name="Julldent Zygo kit", price=25995)
    assert structured_match(box, kit).verdict is StructuredVerdict.REJECTED
    # But a same-KIND near-name with a sane price still matches (no false reject).
    reel = structured_match(
        ProductRecord(name="Meril Filasilk Silk Suture Reel #2-0 - 25Mtr",
                      price=395, description="silk suture"),
        ProductRecord(name="Meril Filasilk #2-0 Silk Suture",
                      price=609, description="silk suture"))
    assert reel.verdict is StructuredVerdict.CONFIRMED


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


def test_instrument_tip_number_discriminates():
    """Hand instruments sharing a code but differing by tip number are different
    ("…-1 EXC32L" vs "…- 6 EXC32L"); "#3" and bare "3" mean the SAME tip; pack
    counts / measurements / code hyphens are not tips."""
    from app.matching.attributes import extract_attributes as ea
    tip = lambda s: ea(N(s)).tip_number
    assert tip("GDC Endo Spoon Excavator -1 Exc32l") == 1
    assert tip("GDC Endo Spoon Excavator - 6 EXC32L") == 6
    assert tip("GDC Excavator #3 EXC32L") == 3 == tip("GDC Excavator 3 EXC32L")  # #3 == 3
    for s in ("Prima Dental Diamond Bur 856-018M (TR-13) - Pack of 3",
              "Julldent Premium Orringer Retractor - 40mm (079C)",
              "Rabbit CIA NiTi Intrusion Archwires Upper 016 X 022 (Pack Of 5)"):
        assert tip(s) is None, s
    # the gate separates different tips, accepts same/compatible tips
    assert gate_check(N("GDC Endo Spoon Excavator -1 Exc32l"),
                      N("GDC Endo Spoon Excavator - 6 EXC32L")).passed is False
    assert gate_check(N("GDC Excavator #3 EXC32L"), N("GDC Excavator 3 EXC32L")).passed is True


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


def test_mojibake_repair_general():
    """Uploaded-sheet encoding corruption is repaired generally (Φ, –, µ, …),
    and clean text is left untouched. Was: "Î¦98*10" → DK NONE."""
    from app.matching.normalize import fix_mojibake
    assert fix_mojibake("Labodent Titanium Disc - Î¦98*10").endswith("Φ98*10")
    assert "–" in fix_mojibake("Retractor â€“ 50mm")
    assert fix_mojibake("Maarc Articulating Paper 40Âµ") == "Maarc Articulating Paper 40µ"
    assert fix_mojibake("GC Gold Label 9 Posterior Restorative") == "GC Gold Label 9 Posterior Restorative"
    assert fix_mojibake("Φ98*10") == "Φ98*10"  # already-clean unicode untouched


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
