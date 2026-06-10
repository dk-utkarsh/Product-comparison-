from app.matching.attributes import (
    extract_attributes,
    extract_attributes_rich,
    extract_brand,
)


def test_extract_brand_takes_first_word_lowercased():
    assert extract_brand("3M Filtek Z350 XT") == "3m"
    assert extract_brand("  GC Fuji IX  ") == "gc"
    assert extract_brand("") == ""


def test_extract_attributes_pack_count():
    a = extract_attributes("Cotton Rolls Pack Of 500")
    assert a.pack_count == 500


def test_extract_attributes_iso_size():
    a = extract_attributes("Endodontic File #25")
    assert a.iso_size == 25


def test_extract_attributes_shade():
    a = extract_attributes("Filtek Z350 XT Shade A2")
    assert a.shade == "a2"


def test_extract_attributes_concentration():
    a = extract_attributes("Chlorhexidine 2% Solution")
    assert a.concentration == 2.0


def test_extract_attributes_model():
    a = extract_attributes("Woodpecker UDS-J Scaler SF-111")
    assert "sf-111" in a.model_codes


def test_extract_attributes_taper():
    a = extract_attributes("ProTaper F2 .06 Taper")
    assert a.taper == "06"


def test_extract_attributes_slot():
    a = extract_attributes("MBT Bracket .022 Slot")
    assert a.slot == "022"


def test_new_fields_default_none():
    a = extract_attributes("3M Filtek Z350")
    assert a.material is None and a.dimension is None and a.wire_form is None


def test_material_from_name():
    a = extract_attributes("OrthoMetric NiTi Thermal Archwire")
    assert a.material == "niti"


def test_dimension_pair_from_name():
    a = extract_attributes("Archwire Rectangular .017 x .025 Lower")
    assert a.dimension == "017x025"
    assert a.wire_form == "lower"


def test_rich_fills_shade_from_description_when_unambiguous():
    a = extract_attributes_rich(
        "GC Fuji IX GP Capsules",
        description="Posterior glass ionomer. Shade A2. Box of 50 capsules.",
    )
    assert a.shade == "a2"
    assert a.pack_count == 50


def test_rich_skips_ambiguous_description_values():
    # description lists every available shade -> must NOT pick one
    a = extract_attributes_rich(
        "GC Fuji IX GP Capsules",
        description="Available in shades A1, A2, A3 and B2.",
    )
    assert a.shade is None


def test_rich_never_overrides_name_attrs():
    a = extract_attributes_rich(
        "Composite A3 syringe",
        description="Also pairs well with shade A2 etch kits.",
    )
    assert a.shade == "a3"
