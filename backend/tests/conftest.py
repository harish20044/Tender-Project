"""Shared test fixtures.

The suite runs against whatever `.env` the developer happens to have, which
means a setting flipped for local use can silently change what the tests
exercise. Anything that would do that is pinned here instead, so a test says
what it depends on rather than inheriting it.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from app.core.config import get_settings


def _reload_settings() -> None:
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def auth_disabled_by_default() -> Iterator[None]:
    """Most tests are not about authentication, so it is off for them.

    Environment variables outrank `.env` in pydantic-settings, so setting it
    here wins whatever the developer has configured locally. Tests that *are*
    about authentication turn it back on with `auth_required`.
    """
    previous = os.environ.get("AUTH_REQUIRED")
    os.environ["AUTH_REQUIRED"] = "false"
    _reload_settings()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("AUTH_REQUIRED", None)
        else:
            os.environ["AUTH_REQUIRED"] = previous
        _reload_settings()


@pytest.fixture
def auth_required() -> Iterator[None]:
    """Turn enforcement on for a test that is about the gate itself."""
    os.environ["AUTH_REQUIRED"] = "true"
    _reload_settings()
    try:
        yield
    finally:
        os.environ["AUTH_REQUIRED"] = "false"
        _reload_settings()
