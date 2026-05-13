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
    possible_threshold: float = 0.55
    variant_threshold: float = 0.45

    score_w_cosine: float = Field(default=0.45)
    score_w_brand: float = Field(default=0.15)
    score_w_pack: float = Field(default=0.05)
    score_w_attr: float = Field(default=0.10)
    score_w_token: float = Field(default=0.15)
    score_w_fuzz: float = Field(default=0.10)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
