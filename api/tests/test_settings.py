from app.settings import Settings


def test_settings_reads_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://test")
    monkeypatch.setenv("EMBED_MODEL", "test-model")
    s = Settings()  # type: ignore[call-arg]
    assert s.database_url == "postgres://test"
    assert s.embed_model == "test-model"


def test_settings_score_weights_default():
    s = Settings(database_url="postgres://x")
    assert s.score_w_cosine == 0.6
    assert s.score_w_brand == 0.2
    assert s.score_w_pack == 0.1
    assert s.score_w_attr == 0.1


def test_settings_thresholds_default():
    s = Settings(database_url="postgres://x")
    assert s.accept_threshold == 0.75
    assert s.possible_threshold == 0.55
    assert s.variant_threshold == 0.45
