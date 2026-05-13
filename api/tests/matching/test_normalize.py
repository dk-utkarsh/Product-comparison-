import pytest

from app.matching.normalize import (
    normalize_for_match,
    strip_noise_suffix,
    strip_pack_suffix,
    strip_sku_tail,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("3M Filtek Z350 XT - SKU: 12345", "3M Filtek Z350 XT"),
        ("GC Fuji IX (SKU: ABC-123)", "GC Fuji IX"),
        ("Dentsply ProTaper - MPN:F2-25", "Dentsply ProTaper"),
        ("Item with no tail", "Item with no tail"),
    ],
)
def test_strip_sku_tail(raw, expected):
    assert strip_sku_tail(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Cotton Rolls Pack Of 500", "Cotton Rolls"),
        ("Burs - Set Of 6", "Burs"),
        ("Capsules (Box Of 50)", "Capsules"),
        ("Cement 25 pcs", "Cement"),
    ],
)
def test_strip_pack_suffix(raw, expected):
    assert strip_pack_suffix(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Composite Resin - Buy Online", "Composite Resin"),
        ("GC Fuji IX | Best Price", "GC Fuji IX"),
        ("Product - Dentalkart.com", "Product"),
        ("Cement - PinkBlue.in", "Cement"),
    ],
)
def test_strip_noise_suffix(raw, expected):
    assert strip_noise_suffix(raw) == expected


def test_normalize_for_match_combined():
    raw = "  3M Filtek Z350 XT   - Pack Of 5  - SKU: ABC-123   "
    assert normalize_for_match(raw) == "3M Filtek Z350 XT"
