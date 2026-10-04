"""Tender listing endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.corpus import store

router = APIRouter(prefix="/api/tenders", tags=["tenders"])


class Tender(BaseModel):
    reference: str
    tender_id: str | None
    title: str
    organisation: str
    published_at: str | None
    closing_at: str | None
    opening_at: str | None
    corrigendum_count: int
    work_category: str | None
    is_construction: bool
    source_listing: str
    scraped_at: str
    # Present once scripts/download_documents.py has archived this tender's
    # pack; absent (None) beforehand. See app.corpus.store.update_tender_document.
    document_key: str | None = None
    document_url: str | None = None
    document_stored_at: str | None = None


class TenderList(BaseModel):
    """The listing plus where and when it came from.

    Provenance travels with the data so the interface can say what it is
    showing and how old it is, rather than presenting figures with no origin.
    """

    tenders: list[Tender]
    count: int
    total_before_filter: int
    scraped_at: str | None
    source: str | None


@router.get("", response_model=TenderList)
def list_tenders(
    construction_only: bool = Query(
        default=False,
        description="Keep only tenders that commission construction work.",
    ),
    category: str | None = Query(default=None, description="Exact work category match."),
    include_closed: bool = Query(
        default=False,
        description="Include tenders whose submission deadline has already passed.",
    ),
    sort: Literal["closing", "published"] = Query(default="closing"),
    limit: int = Query(default=200, ge=1, le=1000),
) -> TenderList:
    data = store.load()
    rows: list[dict[str, object]] = list(data.get("tenders", []))
    total = len(rows)

    if construction_only:
        rows = [row for row in rows if row.get("is_construction")]

    if category:
        rows = [row for row in rows if row.get("work_category") == category]

    if not include_closed:
        # Sorting by soonest deadline otherwise puts the tenders nobody can
        # bid on at the top of the page. Rows are never deleted — the scraper
        # upserts — so without this the listing silently fills with the past.
        today = datetime.now(UTC).date().isoformat()
        rows = [
            row for row in rows if not row.get("closing_at") or str(row["closing_at"])[:10] >= today
        ]

    # Rows with no parsable date sort last rather than crashing the comparison
    # or silently jumping to the front.
    key = "closing_at" if sort == "closing" else "published_at"
    rows.sort(key=lambda row: (row.get(key) is None, row.get(key) or ""))

    if sort == "published":
        rows.reverse()

    return TenderList(
        tenders=[Tender(**row) for row in rows[:limit]],  # type: ignore[arg-type]
        count=min(len(rows), limit),
        total_before_filter=total,
        scraped_at=data.get("scraped_at"),
        source=data.get("source"),
    )


class CategoryCount(BaseModel):
    category: str
    count: int


@router.get("/categories", response_model=list[CategoryCount])
def list_categories() -> list[CategoryCount]:
    """Work categories present in the current data, most common first."""
    counts: dict[str, int] = {}

    for row in store.load().get("tenders", []):
        category = row.get("work_category")
        if category:
            counts[category] = counts.get(category, 0) + 1

    return [
        CategoryCount(category=name, count=count)
        for name, count in sorted(counts.items(), key=lambda item: -item[1])
    ]
