"""Application settings, loaded from the environment.

Every tunable lives here rather than being read from ``os.environ`` at the
point of use, so the full configuration surface is visible in one place and
can be overridden wholesale in tests.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchored to this file rather than the working directory. The API is started
# from backend/, scripts run from the repo root, and tests run from either, so
# a bare ".env" resolves differently depending on who launched the process.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_ENV_FILES = (_REPO_ROOT / ".env", _REPO_ROOT / "backend" / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application -------------------------------------------------------
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173"

    # --- Database ----------------------------------------------------------
    # Optional so the API can serve the tender listing, which reads a scraped
    # file, on a machine with no database. Code that needs it raises a clear
    # error rather than the app refusing to start at all.
    database_url: PostgresDsn | None = None

    # --- Redis -------------------------------------------------------------
    # Optional, because a machine that cannot run Docker cannot run Redis
    # either. With no broker configured, Celery runs tasks eagerly in-process.
    redis_url: RedisDsn | None = None
    celery_broker_url: RedisDsn | None = None
    celery_result_backend: RedisDsn | None = None

    # --- Object storage ----------------------------------------------------
    # "filesystem" is the development fallback where MinIO is unavailable.
    storage_backend: Literal["s3", "filesystem"] = "s3"
    storage_path: str = "./var/documents"

    s3_endpoint_url: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "tender-documents"
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

    @field_validator("redis_url", "celery_broker_url", "celery_result_backend", mode="before")
    @classmethod
    def _empty_string_means_unset(cls, value: object) -> object:
        """Treat a blank value in .env as absent.

        Commenting a line out is awkward in a file people copy and edit, so the
        documented way to disable the broker is to leave the value empty.
        Pydantic would otherwise reject "" as a malformed URL rather than
        reading it as "not configured".
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def providers_configured(self) -> bool:
        """False in CI and unit tests, where fake providers are injected instead."""
        return bool(self.groq_api_key and self.jina_api_key)

    @property
    def celery_eager(self) -> bool:
        """Run tasks inline when there is no broker to hand them to.

        Ingestion then blocks the caller, which is wrong for production but
        makes the pipeline developable and debuggable without Redis.
        """
        return self.celery_broker_url is None


@lru_cache
def get_settings() -> Settings:
    """Cached so the environment is read once per process.

    The ignore is for Pylance/Pyright, which cannot see that pydantic-settings
    fills required fields from the environment and so reports every one of them
    as a missing argument. Mypy's pydantic plugin does understand this, which is
    why warn_unused_ignores is disabled for the project.
    """
    return Settings()  # type: ignore[call-arg]
