from app.matching.query_builder import ProductContext, extract_smart_queries


def test_basic_progressive_queries():
    qs = extract_smart_queries(
        "3M Filtek Z350 XT Shade A2 - Pack Of 5",
        ProductContext(brand="3M"),
    )
    # Most specific contains brand and product line tokens
    assert any("3M" in q and "Z350" in q for q in qs)
    # At least one query should be much shorter than the original
    assert any(len(q.split()) <= 3 for q in qs)


def test_uses_description_for_product_type():
    qs = extract_smart_queries(
        "OrthoMetric Flexy NiTi Thermal 35C Round Archwire - 018 Upper",
        ProductContext(description="Round archwire for orthodontic alignment"),
    )
    # archwire is in PRODUCT_TYPES — should land in some query
    joined = " | ".join(qs).lower()
    assert "archwire" in joined


def test_extracts_product_code():
    qs = extract_smart_queries("Wizdent Master Refill SF-1234", ProductContext())
    assert any("SF-1234" in q for q in qs)


def test_extracts_paren_code():
    qs = extract_smart_queries("GDC Scissors Iris Tc - Curved (S5083)", ProductContext())
    assert any("S5083" in q for q in qs)


def test_uses_sku_from_context():
    qs = extract_smart_queries("Some Product Name", ProductContext(sku="FX-2387"))
    assert any("FX-2387" in q for q in qs)


def test_dedupes_case_insensitively():
    qs = extract_smart_queries("GC Fuji IX", ProductContext(brand="GC"))
    lowered = [q.lower() for q in qs]
    assert len(lowered) == len(set(lowered))


def test_no_brand_query_uses_product_line():
    qs = extract_smart_queries(
        "OrthoMetric Flexy NiTi Thermal Round Archwire",
        ProductContext(),
    )
    # at least one query without the leading brand
    assert any(not q.lower().startswith("orthometric") for q in qs)
