from app.settings import Settings


def test_settings_reads_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://test")
    monkeypatch.setenv("EMBED_MODEL", "test-model")
    s = Settings()  # type: ignore[call-arg]
    assert s.database_url == "postgres://test"
    assert s.embed_model == "test-model"


def test_settings_score_weights_default():
    s = Settings(database_url="postgres://x")
    assert s.score_w_cosine == 0.45
    assert s.score_w_brand == 0.15
    assert s.score_w_pack == 0.05
    assert s.score_w_attr == 0.10
    assert s.score_w_token == 0.15
    assert s.score_w_fuzz == 0.10
    assert (
        s.score_w_cosine
        + s.score_w_brand
        + s.score_w_pack
        + s.score_w_attr
        + s.score_w_token
        + s.score_w_fuzz
    ) == 1.0


def test_settings_thresholds_default():
    s = Settings(database_url="postgres://x")
    assert s.accept_threshold == 0.75
    assert s.possible_threshold == 0.62
    assert s.variant_threshold == 0.45


def test_settings_price_band_default():
    s = Settings(database_url="postgres://x")
    assert s.price_band_max_ratio == 5.0
