"""Use the operating system's certificate store for outbound TLS.

Networks that inspect TLS — most corporate ones — re-sign every certificate
with an internal root CA. That CA is installed in the OS trust store, but
Python ships its own bundled list and does not consult it, so every HTTPS
call out to Groq, Jina or Supabase fails with CERTIFICATE_VERIFY_FAILED on
an otherwise perfectly working machine.

``truststore`` points Python's TLS at the OS store instead, which trusts the
interception CA because the machine already does. That keeps verification on,
rather than the usual workaround of disabling it.

Called explicitly from each entry point rather than on import, so nothing
changes global TLS behaviour merely because a module was imported.
"""

from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger(__name__)

_INSTALLED = False


def install_system_trust() -> bool:
    """Route TLS verification through the OS trust store. Idempotent.

    Returns whether system trust is in effect. A failure here is not fatal:
    on a network that does not intercept TLS the bundled CA list works fine,
    so the caller carries on and any real problem surfaces at the first
    request instead.
    """
    global _INSTALLED
    if _INSTALLED:
        return True

    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception as exc:
        logger.warning("system_trust_unavailable", error=str(exc))
        return False

    _INSTALLED = True
    logger.info("system_trust_installed")
    return True
