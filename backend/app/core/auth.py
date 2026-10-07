"""Verifying Supabase access tokens, and knowing who is asking.

Supabase signs access tokens with an asymmetric key (ES256) and publishes the
public half at the project's JWKS endpoint. Verification therefore needs no
shared secret at all — there is nothing here that would be damaging to leak,
which is why the legacy ``SUPABASE_JWT_SECRET`` plays no part.

Authorization comes from ``app_metadata.role`` rather than ``user_metadata``:
only the service-role key can write app_metadata, while user_metadata is
editable by the user whose token it is. Trusting the latter would let any
signed-in account promote itself to admin.

A verified token is mirrored into the local ``users`` table so the rest of the
schema has a Postgres row to foreign-key against. Supabase remains the source
of truth for the credential and the role; this copy exists for joins.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
from fastapi import Depends, HTTPException, Request, status
from jose import jwt
from jose.exceptions import JWTError

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db import models
from app.db.session import session_scope

logger = get_logger(__name__)

# Signing keys rotate rarely, and every request would otherwise pay for a
# round trip to fetch them. An unrecognised key id forces a refetch anyway, so
# a rotation is picked up on the first token signed by the new key rather than
# after this expires.
_JWKS_TTL_SECONDS = 3600

_jwks_cache: dict[str, Any] | None = None
_jwks_fetched_at: float = 0.0


class AuthError(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


@dataclass(frozen=True)
class Principal:
    """Who is making this request."""

    id: uuid.UUID
    email: str
    role: models.UserRole

    def can(self, *roles: models.UserRole) -> bool:
        return self.role in roles


def _jwks(force: bool = False) -> dict[str, Any]:
    global _jwks_cache, _jwks_fetched_at

    fresh = _jwks_cache is not None and (time.time() - _jwks_fetched_at) < _JWKS_TTL_SECONDS
    if fresh and not force:
        assert _jwks_cache is not None
        return _jwks_cache

    settings = get_settings()
    url = f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
    try:
        response = httpx.get(url, timeout=15.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise AuthError(f"Could not fetch the signing keys: {exc}") from exc

    _jwks_cache = dict(response.json())
    _jwks_fetched_at = time.time()
    logger.info("jwks_fetched", keys=len(_jwks_cache.get("keys", [])))
    return _jwks_cache


def _key_for(token: str) -> dict[str, Any]:
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except JWTError as exc:
        raise AuthError("That token is not a readable JWT.") from exc

    for attempt in (False, True):
        for key in _jwks(force=attempt).get("keys", []):
            if key.get("kid") == kid:
                return dict(key)
        # Unknown key id: the project may have rotated, so refetch once before
        # rejecting rather than failing every request until the cache expires.
    raise AuthError("That token was signed by an unknown key.")


def verify_token(token: str) -> dict[str, Any]:
    """Check a Supabase access token and return its claims."""
    settings = get_settings()
    try:
        return dict(
            jwt.decode(
                token,
                _key_for(token),
                algorithms=["ES256", "RS256"],
                audience="authenticated",
                issuer=f"{settings.supabase_url.rstrip('/')}/auth/v1",
            )
        )
    except JWTError as exc:
        raise AuthError(f"That token is not valid: {exc}") from exc


def _role_from(claims: dict[str, Any]) -> models.UserRole:
    """The role, taken only from server-writable metadata.

    Anything unrecognised becomes the least privileged role rather than
    raising: a new role added in Supabase before it exists here should reduce
    what someone can do, never grant them more.
    """
    raw = str((claims.get("app_metadata") or {}).get("role") or "").strip().lower()
    try:
        return models.UserRole(raw)
    except ValueError:
        if raw:
            logger.warning("unknown_role_claim", role=raw)
        return models.UserRole.VIEWER


def sync_user(claims: dict[str, Any]) -> Principal:
    """Mirror a verified token into the local users table."""
    user_id = uuid.UUID(str(claims["sub"]))
    email = str(claims.get("email") or "")
    role = _role_from(claims)

    with session_scope() as session:
        user = session.get(models.User, user_id)
        if user is None:
            user = models.User(id=user_id, email=email or f"{user_id}@unknown.local")
            session.add(user)
            logger.info("user_first_seen", user_id=str(user_id), role=str(role))
        if email:
            user.email = email
        # The local column is a cache of app_metadata, refreshed on every
        # request so a role changed in Supabase takes effect immediately
        # rather than at the next login.
        user.role = role
        user.last_seen_at = datetime.now(UTC)

    return Principal(id=user_id, email=email, role=role)


def _bearer(request: Request) -> str | None:
    header = request.headers.get("Authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


def optional_user(request: Request) -> Principal | None:
    """The caller, when they presented a valid token. None otherwise.

    Used where a route works unauthenticated but should still attribute what
    it does when it can.
    """
    token = _bearer(request)
    if not token:
        return None
    try:
        return sync_user(verify_token(token))
    except HTTPException:
        return None


def current_user(request: Request) -> Principal | None:
    """The caller, or None when anonymous access is allowed.

    A token that is presented is always verified — a forged or expired one is
    rejected whatever the configuration says. AUTH_REQUIRED only decides
    whether a request carrying *no* token is turned away or served
    anonymously, which is what lets the interface be used before every client
    signs in without weakening what a token means.
    """
    token = _bearer(request)
    if not token:
        if get_settings().auth_required:
            raise AuthError("This endpoint needs a Supabase access token.")
        return None
    return sync_user(verify_token(token))


def authenticated_user(request: Request) -> Principal:
    """The caller, always. For routes that are meaningless without one.

    Unlike `current_user`, this ignores AUTH_REQUIRED: "who am I" has no
    anonymous answer regardless of how the rest of the API is configured.
    """
    token = _bearer(request)
    if not token:
        raise AuthError("This endpoint needs a Supabase access token.")
    return sync_user(verify_token(token))


CurrentUser = Annotated[Principal | None, Depends(current_user)]
AuthenticatedUser = Annotated[Principal, Depends(authenticated_user)]
OptionalUser = Annotated[Principal | None, Depends(optional_user)]


def require(*roles: models.UserRole) -> Any:
    """Dependency admitting only the listed roles.

    Admin is admitted everywhere without being listed, so adding a role to a
    route cannot accidentally lock administrators out of it.
    """
    allowed = set(roles) | {models.UserRole.ADMIN}

    def guard(user: CurrentUser) -> Principal | None:
        if user is None:
            # Anonymous, and anonymous access is permitted; there is no role
            # to check against.
            return None
        if user.role not in allowed:
            logger.info("forbidden", user=user.email, role=str(user.role))
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"This needs one of: {', '.join(sorted(r.value for r in allowed))}. "
                    f"You are {user.role.value}."
                ),
            )
        return user

    return Depends(guard)


# Reachable without a token, whatever AUTH_REQUIRED says. Everything else is
# protected by default, so a route added later is covered without anyone
# having to remember to cover it — the failure mode of per-route opt-in is a
# forgotten route, and the forgotten one is always the interesting one.
PUBLIC_PATHS = frozenset(
    {
        "/health",
        "/api/auth/config",
        "/api/auth/login",
        # FastAPI's own documentation surface.
        "/docs",
        "/redoc",
        "/openapi.json",
    }
)


def enforce_auth(request: Request) -> None:
    """Gate every request that is not explicitly public.

    Applied once, to the whole application, rather than route by route.
    Roles are still checked per route by `require`; this only settles whether
    an anonymous caller gets through the door at all.
    """
    if not get_settings().auth_required:
        return
    # A CORS preflight carries no credentials by design and must not be
    # answered with a 401, or the real request is never sent.
    if request.method == "OPTIONS":
        return
    if request.url.path in PUBLIC_PATHS:
        return

    token = _bearer(request)
    if not token:
        raise AuthError("This endpoint needs a Supabase access token.")
    verify_token(token)


def record_activity(
    user: Principal | None,
    action: models.ActivityAction,
    *,
    tender_id: str | None = None,
    request: Request | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Note what someone did. Never raises.

    An audit write failing must not fail the thing being audited — losing a
    log line is a far smaller problem than refusing a legitimate request.
    """
    if user is None:
        return
    try:
        with session_scope() as session:
            session.add(
                models.UserActivityLog(
                    user_id=user.id,
                    action=action,
                    tender_id=uuid.UUID(tender_id) if tender_id else None,
                    ip_address=(request.client.host if request and request.client else None),
                    user_agent=(request.headers.get("user-agent") if request else None),
                    extra=extra,
                )
            )
    except Exception as exc:
        logger.warning("activity_not_recorded", action=str(action), error=str(exc))


def record_question(
    user: Principal | None,
    question: str,
    *,
    tender_id: str | None,
    source: models.QuestionSource,
    answerable: bool,
    confidence: float | None = None,
    response_ms: int | None = None,
    faq_key: str | None = None,
) -> None:
    """Log a question so recurring ones can be found later. Never raises.

    The normalised form is what makes that possible: the same question asked
    twenty different ways should cluster, which is the signal for promoting it
    into the standard set.
    """
    if user is None:
        return
    try:
        normalised = " ".join(question.lower().split())
        with session_scope() as session:
            session.add(
                models.QuestionLog(
                    user_id=user.id,
                    tender_id=uuid.UUID(tender_id) if tender_id else None,
                    question_text=question,
                    normalized_question=normalised,
                    source=source,
                    matched_faq_key=faq_key,
                    was_answerable=answerable,
                    confidence=confidence,
                    response_time_ms=response_ms,
                )
            )
    except Exception as exc:
        logger.warning("question_not_logged", error=str(exc))
