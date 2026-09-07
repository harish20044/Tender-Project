"""Tests for settings resolution.

The blank-broker case has its own test because RUNNING.md tells people to
disable Celery by leaving the value empty. If that stops working, the
documented setup path breaks at startup with a validation error that reads
like a malformed URL rather than a missing feature.
"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings

BASE = {
    "database_url": "postgresql+psycopg://u:p@localhost:5432/db",
}


def test_blank_broker_disables_celery() -> None:
    settings = Settings(**BASE, redis_url="", celery_broker_url="", celery_result_backend="")

    assert settings.redis_url is None
    assert settings.celery_broker_url is None
    assert settings.celery_eager is True


def test_whitespace_only_broker_also_counts_as_unset() -> None:
    settings = Settings(**BASE, celery_broker_url="   ")

    assert settings.celery_broker_url is None
    assert settings.celery_eager is True


def test_configured_broker_disables_eager_mode() -> None:
    settings = Settings(**BASE, celery_broker_url="redis://localhost:6379/1")

    assert settings.celery_eager is False


def test_a_malformed_broker_url_is_still_rejected() -> None:
    # Blank means "unset"; nonsense should still fail loudly rather than being
    # quietly swallowed by the same escape hatch.
    with pytest.raises(ValidationError):
        Settings(**BASE, celery_broker_url="not-a-url")


def test_cors_origins_are_split_and_trimmed() -> None:
    settings = Settings(**BASE, cors_origins="http://a.test, http://b.test ,")

    assert settings.cors_origin_list == ["http://a.test", "http://b.test"]


def test_providers_are_reported_unconfigured_without_both_keys() -> None:
    assert Settings(**BASE, groq_api_key="x", jina_api_key="").providers_configured is False
    assert Settings(**BASE, groq_api_key="x", jina_api_key="y").providers_configured is True
