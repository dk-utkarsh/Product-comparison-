from app.matching.tokens import (
    distinguishing_tokens,
    fuzz_ratio,
    tokenize,
    weighted_overlap,
)


def test_tokenize_lowercases_and_strips_punct():
    assert tokenize("3M Filtek Z350 XT - A2") == ["3m", "filtek", "z350", "xt", "a2"]


def test_distinguishing_drops_stopwords():
    out = distinguishing_tokens("Dental Composite Pack of 5 Z350 XT")
    assert "z350" in out
    assert "xt" in out
    assert "dental" not in out
    assert "pack" not in out


def test_weighted_overlap_full_match():
    assert weighted_overlap("3M Filtek Z350", "3M Filtek Z350") == 1.0


def test_weighted_overlap_only_stopwords_low():
    s = weighted_overlap("Dental Product India", "Dental Product Brand X")
    assert s < 0.5


def test_weighted_overlap_distinguishing_high():
    s = weighted_overlap("Wizdent Composite", "Wizdent Master Composite Refills")
    assert s > 0.4


def test_fuzz_ratio_handles_reordering():
    # token_set_ratio is order-insensitive
    r = fuzz_ratio("Filtek 3M Z350 XT A2", "3M Filtek Z350 XT Shade A2")
    assert r > 0.85


def test_fuzz_ratio_empty():
    assert fuzz_ratio("", "x") == 0.0
    assert fuzz_ratio("x", "") == 0.0
