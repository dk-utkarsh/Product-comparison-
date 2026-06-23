from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str
    embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embed_device: str = "cpu"

    accept_threshold: float = 0.75
    possible_threshold: float = 0.62
    variant_threshold: float = 0.45

    # Reject a match outright when the competitor price differs from DK by
    # more than this multiple (or less than 1/multiple). A "compressor
    # valve" at ₹250 and an "air compressor" at ₹22,512 are not the same
    # product no matter how cosine-similar their names look.
    price_band_max_ratio: float = 5.0

    # ── Exact-match pipeline ────────────────────────────────
    # LLM borderline judge (Approach C). Empty key disables the judge;
    # the pipeline then runs as pure rules (Approach A).
    anthropic_api_key: str = ""
    llm_judge_model: str = "claude-haiku-4-5"
    llm_judge_budget_per_run: int = 30

    # How many top triaged candidates per competitor get a PDP fetch. These
    # fetch concurrently, so a slightly larger K barely affects latency but
    # gives the right sub-variant a slot when several siblings score similarly.
    pdp_top_k: int = 5

    # Structured-match CONFIRMED gates: product line must agree strongly.
    confirm_cosine: float = 0.80
    confirm_fuzz: float = 0.85

    # ── Scheduled SKU runs (DentalKart admin catalog API) ───
    dk_admin_products_url: str = "https://serverless-prod.dentalkart.com/api/v1/products/list/view"
    dk_admin_api_key: str = ""          # x-api-key for the admin product API
    scheduled_skus_per_run: int = 50
    # Of each run, this many are a FIXED watchlist (same products every run) so
    # the price-history/comparison feature has a continuous series; the rest are
    # fresh random. Seeded once from random, then kept constant.
    scheduled_watchlist_size: int = 5
    scheduled_run_times: str = "10:00,11:30,13:00,14:30,16:00"  # IST, comma-sep
    scheduled_run_tz: str = "Asia/Kolkata"
    scheduled_runs_enabled: bool = False  # turn the scheduler on
    runs_retention_days: int = 30
    runs_db_path: str = "data/runs.sqlite3"

    score_w_cosine: float = Field(default=0.45)
    score_w_brand: float = Field(default=0.15)
    score_w_pack: float = Field(default=0.05)
    score_w_attr: float = Field(default=0.10)
    score_w_token: float = Field(default=0.15)
    score_w_fuzz: float = Field(default=0.10)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
