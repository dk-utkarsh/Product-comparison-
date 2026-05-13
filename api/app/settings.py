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

    score_w_cosine: float = Field(default=0.6)
    score_w_brand: float = Field(default=0.2)
    score_w_pack: float = Field(default=0.1)
    score_w_attr: float = Field(default=0.1)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
