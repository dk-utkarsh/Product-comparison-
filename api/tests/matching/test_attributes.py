from app.matching.attributes import (
    extract_attributes,
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
