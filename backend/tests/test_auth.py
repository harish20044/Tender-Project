"""Tests for the authentication gate.

The property worth pinning is that protection is the default. Routes are not
individually opted in, because the one that gets forgotten is always the
interesting one — so these assert that an arbitrary endpoint is closed when
enforcement is on, and that only a named few are open.

No network: token verification is exercised through its own failure paths
rather than against live Supabase keys.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.auth import PUBLIC_PATHS, AuthError, _role_from
from app.db import models
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# --- the gate -------------------------------------------------------------- #


@pytest.mark.parametrize(
    "path",
    ["/api/tenders", "/api/documents", "/api/tenders/categories"],
)
def test_endpoints_are_closed_when_enforcement_is_on(
    client: TestClient, auth_required: None, path: str
) -> None:
    assert client.get(path).status_code == 401


def test_the_same_endpoints_are_open_when_enforcement_is_off(client: TestClient) -> None:
    # The autouse fixture leaves enforcement off, which is how every other
    # test in the suite reaches these routes at all.
    assert client.get("/api/tenders").status_code == 200


def test_health_stays_reachable_so_orchestrators_can_still_probe_it(
    client: TestClient, auth_required: None
) -> None:
    # Compose's healthcheck has no credentials; gating this would mark a
    # perfectly healthy container unhealthy and restart it forever.
    assert client.get("/health").status_code == 200


def test_the_sign_in_route_is_reachable_without_being_signed_in(
    client: TestClient, auth_required: None
) -> None:
    # Posting no body is a validation error, not a 401 — which is the point:
    # the request reached the route rather than being turned away at the door.
    assert client.post("/api/auth/login", json={}).status_code != 401


def test_the_interface_can_ask_whether_sign_in_is_required(
    client: TestClient, auth_required: None
) -> None:
    response = client.get("/api/auth/config")

    assert response.status_code == 200
    assert response.json()["auth_required"] is True


def test_a_forged_token_is_refused(client: TestClient, auth_required: None) -> None:
    response = client.get("/api/tenders", headers={"Authorization": "Bearer not.a.real.token"})

    assert response.status_code == 401


def test_a_bearer_prefix_without_a_token_is_refused(
    client: TestClient, auth_required: None
) -> None:
    assert client.get("/api/tenders", headers={"Authorization": "Bearer "}).status_code == 401


def test_identity_needs_a_token_even_when_enforcement_is_off(client: TestClient) -> None:
    # "Who am I" has no anonymous answer regardless of configuration.
    assert client.get("/api/auth/me").status_code == 401


def test_the_public_list_is_small_and_deliberate() -> None:
    # A path added here is a path nobody has to authenticate for, so growth
    # should be visible in review rather than incidental.
    assert {
        "/health",
        "/api/auth/config",
        "/api/auth/login",
        "/docs",
        "/redoc",
        "/openapi.json",
    } == PUBLIC_PATHS


# --- roles ----------------------------------------------------------------- #


def test_the_role_is_read_from_server_written_metadata() -> None:
    claims = {"app_metadata": {"role": "manager"}, "user_metadata": {"role": "admin"}}

    # user_metadata is editable by the user whose token it is; trusting it
    # would let any account promote itself.
    assert _role_from(claims) is models.UserRole.MANAGER


def test_a_missing_role_is_the_least_privileged_one() -> None:
    assert _role_from({}) is models.UserRole.VIEWER
    assert _role_from({"app_metadata": {}}) is models.UserRole.VIEWER


def test_an_unrecognised_role_reduces_rather_than_grants() -> None:
    # A role added in Supabase before it exists here must not become admin by
    # accident.
    assert _role_from({"app_metadata": {"role": "superuser"}}) is models.UserRole.VIEWER


def test_role_matching_ignores_case_and_padding() -> None:
    assert _role_from({"app_metadata": {"role": " Admin "}}) is models.UserRole.ADMIN


def test_an_auth_error_tells_the_client_how_to_authenticate() -> None:
    error = AuthError("nope")

    assert error.status_code == 401
    assert error.headers is not None
    assert error.headers.get("WWW-Authenticate") == "Bearer"
