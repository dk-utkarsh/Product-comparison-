from app.matching.attributes import Attributes
from app.matching.score import Verdict, score_match


def test_high_cosine_same_brand_passes_accept():
    # Realistic: when cosine is high, token overlap and fuzz are also high.
    r = score_match(
        cosine_sim=0.95,
        search_attrs=Attributes(brand="3m"),
        candidate_attrs=Attributes(brand="3m"),
        token_overlap=0.9,
        fuzz_ratio=0.95,
    )
    assert r.verdict == Verdict.CONFIRMED
    assert r.score >= 0.75


def test_mid_cosine_landing_in_possible():
    r = score_match(
        cosine_sim=0.55,
        search_attrs=Attributes(brand="gc"),
        candidate_attrs=Attributes(brand="gc"),
        token_overlap=0.5,
        fuzz_ratio=0.6,
    )
    assert r.verdict in (Verdict.POSSIBLE, Verdict.VARIANT)


def test_low_cosine_brand_mismatch_rejects():
    r = score_match(
        cosine_sim=0.2,
        search_attrs=Attributes(brand="3m"),
        candidate_attrs=Attributes(brand="gc"),
        token_overlap=0.1,
        fuzz_ratio=0.2,
    )
    assert r.verdict == Verdict.REJECTED


def test_pack_match_perfect_within_2pct():
    r = score_match(
        cosine_sim=0.9,
        search_attrs=Attributes(brand="x", pack_count=500),
        candidate_attrs=Attributes(brand="x", pack_count=505),
        token_overlap=0.85,
        fuzz_ratio=0.9,
    )
    assert r.verdict == Verdict.CONFIRMED


def test_pack_mismatch_lowers_score():
    r_match = score_match(
        cosine_sim=0.9,
        search_attrs=Attributes(brand="x", pack_count=10),
        candidate_attrs=Attributes(brand="x", pack_count=10),
        token_overlap=0.9,
        fuzz_ratio=0.9,
    )
    r_mismatch = score_match(
        cosine_sim=0.9,
        search_attrs=Attributes(brand="x", pack_count=10),
        candidate_attrs=Attributes(brand="x", pack_count=100),
        token_overlap=0.9,
        fuzz_ratio=0.9,
    )
    assert r_match.score > r_mismatch.score
