"""Application settings, loaded from the environment.

Every tunable lives here rather than being read from ``os.environ`` at the
point of use, so the full configuration surface is visible in one place and
can be overridden wholesale in tests.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application -------------------------------------------------------
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173"

    # --- Database ----------------------------------------------------------
    database_url: PostgresDsn

    # --- Redis -------------------------------------------------------------
    redis_url: RedisDsn
    celery_broker_url: RedisDsn
    celery_result_backend: RedisDsn

    # --- Object storage ----------------------------------------------------
    s3_endpoint_url: str
    s3_access_key: str
    s3_secret_key: str
    s3_bucket: str
    s3_region: str = "us-east-1"

    # --- Groq --------------------------------------------------------------
    groq_api_key: str = ""
    # Verified against the live account. Groq's catalogue changes, so confirm
    # with GET /openai/v1/models rather than assuming a model is still served.
    groq_model_primary: str = "openai/gpt-oss-120b"
    groq_model_fast: str = "openai/gpt-oss-20b"
    # Both gpt-oss models reason before answering and bill those tokens as
    # completion. "low" reaches the same answer as "high" on extraction at
    # roughly a quarter of the reasoning cost.
    groq_reasoning_effort: Literal["low", "medium", "high"] = "low"
    groq_max_rpm: int = 25
    groq_max_tpm: int = 6000
    groq_max_retries: int = 5
    groq_timeout_seconds: int = 120

    # --- Jina --------------------------------------------------------------
    jina_api_key: str = ""
    jina_embed_model: str = "jina-embeddings-v3"
    jina_rerank_model: str = "jina-reranker-v2-base-multilingual"
    jina_embed_dimensions: int = 512
    jina_embed_batch_size: int = 64

    # --- Keycloak ----------------------------------------------------------
    keycloak_url: str = "http://keycloak:8080"
    keycloak_realm: str = "tender"
    keycloak_client_id: str = "tender-api"
    keycloak_client_secret: str = ""

    # --- Pipeline ----------------------------------------------------------
    max_document_pages: int = 1200
    ocr_languages: str = "eng"
    ocr_text_threshold_chars: int = 80
    chunk_target_tokens: int = 700
    chunk_overlap_tokens: int = 100

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def providers_configured(self) -> bool:
        """False in CI and unit tests, where fake providers are injected instead."""
        return bool(self.groq_api_key and self.jina_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
