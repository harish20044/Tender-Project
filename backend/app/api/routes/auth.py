"""Signing in, and finding out who you are.

Credentials are exchanged through this API rather than from the browser
directly to Supabase. That keeps every Supabase key server-side — the browser
bundle carries none at all — and gives the login a server-side moment where it
can be recorded, which is where the activity trail starts.

Only the token exchange goes through here. Once issued, the token is verified
against Supabase's public keys on every request, so this endpoint is not in
the path of anything that follows.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.core.auth import AuthenticatedUser, Principal, record_activity, sync_user, verify_token
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db import models
from app.db.session import session_scope

logger = get_logger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    # Plain str rather than EmailStr: Supabase is the authority on whether an
    # address exists, and pulling in a validator to pre-check the format would
    # only duplicate a rejection we already handle.
    email: Annotated[str, Field(min_length=3, max_length=320)]
    password: Annotated[str, Field(min_length=1)]


class Me(BaseModel):
    id: str
    email: str
    role: str


class LoginResponse(BaseModel):
    access_token: str
    expires_in: int
    user: Me


def _supabase() -> tuple[str, str]:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase is not configured; sign-in is unavailable.",
        )
    return settings.supabase_url.rstrip("/"), settings.supabase_service_role_key


class AuthConfig(BaseModel):
    auth_required: bool
    sign_in_available: bool


@router.get("/config", response_model=AuthConfig)
def config() -> AuthConfig:
    """What the interface needs to know before anyone has signed in.

    The interface should not carry its own opinion about whether sign-in is
    mandatory — two copies of that setting would eventually disagree, and the
    one that matters is the server's. Deliberately unauthenticated: it is
    asked precisely when there is no token.
    """
    settings = get_settings()
    return AuthConfig(
        auth_required=settings.auth_required,
        sign_in_available=bool(settings.supabase_url and settings.supabase_service_role_key),
    )


@router.post("/login", response_model=LoginResponse)
async def login(credentials: LoginRequest, request: Request) -> LoginResponse:
    """Exchange an email and password for an access token."""
    base, key = _supabase()

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(
                f"{base}/auth/v1/token?grant_type=password",
                headers={"apikey": key, "Content-Type": "application/json"},
                json={"email": credentials.email, "password": credentials.password},
            )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Could not reach the authentication service: {exc}",
            ) from exc

    if response.status_code != 200:
        # Deliberately not echoing the upstream message: it distinguishes
        # "no such user" from "wrong password", which tells an attacker
        # which half they have right.
        logger.info("login_rejected", email=credentials.email, status=response.status_code)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="That email and password combination was not accepted.",
        )

    payload = response.json()
    token = str(payload.get("access_token") or "")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The authentication service returned no token.",
        )

    # Verified here rather than trusted: it also mirrors the user locally, so
    # the row the activity log points at exists before the log is written.
    principal: Principal = sync_user(verify_token(token))
    record_activity(principal, models.ActivityAction.LOGIN, request=request)

    with_login_count(principal)

    logger.info("login", email=principal.email, role=str(principal.role))
    return LoginResponse(
        access_token=token,
        expires_in=int(payload.get("expires_in") or 3600),
        user=Me(id=str(principal.id), email=principal.email, role=principal.role.value),
    )


def with_login_count(principal: Principal) -> None:
    """Record that this was a sign-in, not merely another request."""
    try:
        with session_scope() as session:
            user = session.get(models.User, principal.id)
            if user is not None:
                user.login_count = (user.login_count or 0) + 1
                user.last_login_at = datetime.now(UTC)
    except Exception as exc:
        logger.warning("login_count_not_recorded", error=str(exc))


@router.get("/me", response_model=Me)
def me(user: AuthenticatedUser) -> Me:
    """Who the presented token belongs to."""
    return Me(id=str(user.id), email=user.email, role=user.role.value)


@router.post("/logout")
def logout(user: AuthenticatedUser, request: Request) -> dict[str, str]:
    """Record a sign-out.

    The token itself is stateless and remains valid until it expires, so this
    does not revoke anything — it marks the end of a session in the activity
    trail, which is what the audit view reads.
    """
    record_activity(user, models.ActivityAction.LOGOUT, request=request)
    return {"status": "signed out"}
