"""Persistence for scraped tenders.

Backed by the ``tenders`` table. This used to be a JSON file — Postgres with
pgvector could not be installed on a Windows machine without a compiler, so
requiring a database to see the dashboard would have meant nobody could run
it. A real, reachable database is a given now, so this module was rewritten
to use it, exactly as its own previous docstring said it eventually would:
"the same two functions move to SQLAlchemy and nothing above this layer
changes." The public interface (``save``, ``load``, ``update_tender_document``)
is unchanged on purpose, so the API route, the scrape script, and the
download script did not need to change with it.

One behavioural difference worth knowing: the old file was replaced wholesale
on every scrape, so a tender that dropped off the portal listing vanished
from the cache. Upserting into a table does not drop rows, so a tender stays
visible (with whatever it last read) even after it closes or is delisted.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.corpus.cppp import ScrapedTender
from app.db import models
from app.db.session import session_scope

logger = get_logger(__name__)

SOURCE = "eprocure.gov.in/cppp"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _upsert(session: Session, tender: ScrapedTender) -> tuple[models.Tender, bool]:
    """Insert or update one row. Returns (row, was_newly_created)."""
    existing = session.scalar(
        select(models.Tender).where(models.Tender.reference_number == tender.reference)
    )
    created = existing is None
    row = existing or models.Tender(reference_number=tender.reference)

    row.title = tender.title
    row.issuing_authority = tender.organisation
    row.published_date = _parse_date(tender.published_at)
    row.closing_date = _parse_datetime(tender.closing_at)
    row.opening_date = _parse_datetime(tender.opening_at)
    row.work_category = tender.work_category
    row.portal_tender_id = tender.tender_id
    row.detail_url = tender.detail_url
    row.corrigendum_count = tender.corrigendum_count
    row.is_construction = tender.is_construction
    row.source_listing = tender.source_listing

    if created:
        session.add(row)
    session.flush()
    return row, created


def save(tenders: list[ScrapedTender], path: object = None) -> dict[str, Any]:
    """Upsert a scrape result. ``path`` is accepted and ignored, kept only so
    callers built for the old file-backed version do not need to change.

    Returns a small summary: how many rows were new versus refreshed, and the
    reference numbers that are new — which is what a caller (the periodic
    scrape task, in particular) uses to know what to queue for download.
    """
    new_references: list[str] = []
    updated = 0

    with session_scope() as session:
        for tender in tenders:
            _row, created = _upsert(session, tender)
            if created:
                new_references.append(tender.reference)
            else:
                updated += 1

    logger.info("tenders_saved", new=len(new_references), updated=updated, count=len(tenders))
    return {
        "count": len(tenders),
        "new": len(new_references),
        "updated": updated,
        "new_references": new_references,
    }


def update_tender_document(reference: str, *, document_key: str, document_url: str) -> bool:
    """Record where a tender's downloaded pack ended up, on its own row.

    Matches on reference number. Returns False when no row matches — a stale
    scrape, not an error.
    """
    with session_scope() as session:
        row = session.scalar(
            select(models.Tender).where(models.Tender.reference_number == reference)
        )
        if row is None:
            logger.warning("tender_document_row_missing", reference=reference)
            return False

        row.archive_key = document_key
        row.archive_url = document_url
        row.archive_stored_at = datetime.now(UTC)

    logger.info("tender_document_recorded", reference=reference, key=document_key)
    return True


def _row_to_dict(row: models.Tender) -> dict[str, Any]:
    return {
        "reference": row.reference_number,
        "tender_id": row.portal_tender_id,
        "title": row.title,
        "organisation": row.issuing_authority,
        "published_at": row.published_date.isoformat() if row.published_date else None,
        "closing_at": row.closing_date.isoformat() if row.closing_date else None,
        "opening_at": row.opening_date.isoformat() if row.opening_date else None,
        "corrigendum_count": row.corrigendum_count,
        "work_category": row.work_category,
        "is_construction": row.is_construction,
        "source_listing": row.source_listing or "",
        "scraped_at": row.updated_at.isoformat(),
        "detail_url": row.detail_url,
        "document_key": row.archive_key,
        "document_url": row.archive_url,
        "document_stored_at": row.archive_stored_at.isoformat() if row.archive_stored_at else None,
    }


def load(path: object = None) -> dict[str, Any]:
    """Every scraped tender, shaped exactly like the old cache file was.

    ``path`` is accepted and ignored, for the same reason as in ``save``.
    Never raises: an empty table reads back as "nothing scraped yet", the
    same as a missing file used to.
    """
    with session_scope() as session:
        rows = session.scalars(
            select(models.Tender).order_by(
                models.Tender.closing_date.is_(None), models.Tender.closing_date
            )
        ).all()
        tenders = [_row_to_dict(row) for row in rows]
        scraped_at = max((row.updated_at for row in rows), default=None)

    return {
        "scraped_at": scraped_at.isoformat() if scraped_at else None,
        "source": SOURCE if tenders else None,
        "count": len(tenders),
        "tenders": tenders,
    }
