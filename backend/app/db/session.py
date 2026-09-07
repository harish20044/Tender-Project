"""Database engine and session management."""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()

    if settings.database_url is None:
        raise RuntimeError(
            "DATABASE_URL is not set. The database is optional only for the "
            "scraped tender listing; anything else needs it configured. See "
            "RUNNING.md for a hosted Postgres that takes a few minutes to set up."
        )

    return create_engine(
        str(settings.database_url),
        # Ingestion workers hold connections for the length of a long task, so
        # the pool is sized for concurrent workers rather than request volume.
        pool_size=10,
        max_overflow=5,
        # Recycle ahead of any idle timeout imposed between the app and the
        # database, which otherwise surfaces as a stale-connection error at the
        # start of a long-running task.
        pool_pre_ping=True,
        pool_recycle=1800,
    )


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency. One session per request, rolled back on error."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for Celery tasks and scripts, which have no request
    lifecycle to hang a session off."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
