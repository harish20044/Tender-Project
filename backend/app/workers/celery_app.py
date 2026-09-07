"""Celery application.

Long jobs do not belong in a request. Parsing a five-hundred-page tender takes
minutes, and every model call is rate limited, so ingestion runs here and the
API reports progress rather than blocking on it.

With no broker configured the app runs eagerly, executing tasks inline in the
calling process. That is wrong for production and right for a machine that
cannot run Redis, so the mode is chosen from configuration rather than being a
separate code path.
"""

from __future__ import annotations

from celery import Celery

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def create_celery() -> Celery:
    settings = get_settings()

    app = Celery("tender_intelligence")

    if settings.celery_eager:
        logger.warning(
            "celery_eager_mode",
            detail=(
                "No broker configured. Tasks will run inline in the calling "
                "process, which blocks the caller. Development only."
            ),
        )
        app.conf.update(task_always_eager=True, task_eager_propagates=True)
    else:
        app.conf.update(
            broker_url=str(settings.celery_broker_url),
            result_backend=str(settings.celery_result_backend or settings.celery_broker_url),
        )

    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        # Ingestion tasks are long and not idempotent halfway through. Acking
        # late means a worker that dies mid-document gives the job back to the
        # queue instead of silently dropping it.
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        # A stuck parse should fail rather than occupy a worker forever.
        task_soft_time_limit=1800,
        task_time_limit=2100,
    )

    # Task modules are imported for their registration side effect. Kept in one
    # place so a worker and the API agree on what exists.
    app.autodiscover_tasks(["app.workers"], force=True)

    return app


celery_app = create_celery()


# Celery ships no types for its task decorator, so mypy sees the wrapped
# function as untyped. The signature below is still checked.
@celery_app.task(name="app.workers.ping")  # type: ignore[untyped-decorator]
def ping() -> str:
    """Smoke test, so a worker can be proven alive without running an ingest."""
    return "pong"
