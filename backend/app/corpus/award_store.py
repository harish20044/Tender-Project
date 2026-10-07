"""Persistence for award-of-contract records.

Awards land in ``historical_tenders``, which already had `awarded_value`,
`winning_bidder`, `bidder_count` and `awarded_date` columns waiting for
them — the corpus was designed around outcomes and had simply never had any.

Two rules shape the writes.

An award is matched to an existing row by reference number where one exists,
so an award for a tender already in the corpus enriches that row rather than
duplicating it. Where no row matches, the award becomes a row of its own:
knowing a comparable job was awarded to a named bidder is useful even without
the original notice.

An award never overwrites a figure with nothing. The portal publishes partial
records — a bidder with no value, a value with no bid count — and a later
scrape of a thinner record must not erase what an earlier one found.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.corpus.awards import AwardRecord, awarded_date_of
from app.db import models
from app.db.session import session_scope

logger = get_logger(__name__)

SOURCE = "eprocure.gov.in/cppp/resultoftendersnew"


def _match(session: Session, record: AwardRecord) -> models.HistoricalTender | None:
    """An existing corpus row for this award, if there is one."""
    if not record.reference:
        return None
    return session.scalar(
        select(models.HistoricalTender).where(
            models.HistoricalTender.reference_number == record.reference
        )
    )


def _apply(row: models.HistoricalTender, record: AwardRecord) -> None:
    """Copy an award onto a corpus row without erasing what is already there."""
    if record.contract_value is not None:
        row.awarded_value = record.contract_value
    if record.selected_bidder:
        row.winning_bidder = record.selected_bidder[:512]
    if record.bids_received is not None:
        row.bidder_count = record.bids_received

    awarded = awarded_date_of(record)
    if awarded is not None:
        row.awarded_date = awarded

    if record.organisation and not row.issuing_authority:
        row.issuing_authority = record.organisation[:512]
    row.source = SOURCE


def save_awards(records: list[AwardRecord]) -> dict[str, Any]:
    """Upsert award records into the historical corpus.

    Returns how many rows were enriched versus created, and how many records
    carried no outcome worth storing.
    """
    enriched = 0
    created = 0
    skipped = 0

    with session_scope() as session:
        for record in records:
            if not record.is_useful:
                skipped += 1
                continue

            row = _match(session, record)
            if row is None:
                row = models.HistoricalTender(
                    reference_number=record.reference,
                    title=record.description or record.reference or "Awarded tender",
                    issuing_authority=(record.organisation or None),
                    # Real, scraped records. Nothing here is augmented, and
                    # the flag is what keeps claims about the corpus honest.
                    is_synthetic=False,
                    source=SOURCE,
                )
                session.add(row)
                session.flush()
                created += 1
            else:
                enriched += 1

            _apply(row, record)

    logger.info(
        "awards_saved",
        created=created,
        enriched=enriched,
        skipped=skipped,
        count=len(records),
    )
    return {
        "count": len(records),
        "created": created,
        "enriched": enriched,
        "skipped_empty": skipped,
    }


def award_coverage() -> dict[str, Any]:
    """How much of the corpus now carries an outcome.

    Reported because it is the honest measure of the comparison feature: a
    corpus of a thousand notices with ten awards compares on scope and
    little else.
    """
    with session_scope() as session:
        rows = session.scalars(select(models.HistoricalTender)).all()
        count = len(rows)
        with_value = sum(1 for row in rows if row.awarded_value is not None)
        with_bidder = sum(1 for row in rows if row.winning_bidder)
        with_count = sum(1 for row in rows if row.bidder_count is not None)

    return {
        "historical_tenders": count,
        "with_awarded_value": with_value,
        "with_winning_bidder": with_bidder,
        "with_bidder_count": with_count,
        "value_coverage": (with_value / count) if count else 0.0,
    }
