"""Tests for the tender listing endpoint's filtering.

The listing is sorted by soonest deadline, and the scraper upserts rather than
replacing, so without an explicit filter the page fills from the top with
tenders nobody can bid on any more. These pin that behaviour.

``store.load`` is stubbed so the test states its own data and needs no
database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _day(offset: int) -> str:
    return (datetime.now(UTC) + timedelta(days=offset)).isoformat()


def _row(reference: str, closing: str | None, **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "reference": reference,
        "tender_id": None,
        "title": f"Construction work {reference}",
        "organisation": "Test Authority",
        "published_at": _day(-30),
        "closing_at": closing,
        "opening_at": None,
        "corrigendum_count": 0,
        "work_category": "Bridges",
        "is_construction": True,
        "source_listing": "high_value",
        "scraped_at": _day(-1),
    }
    row.update(extra)
    return row


@pytest.fixture
def listing(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        _row("EXPIRED-1", _day(-9)),
        _row("EXPIRED-2", _day(-2)),
        _row("LIVE-1", _day(4)),
        _row("LIVE-2", _day(30)),
        _row("NO-DEADLINE", None),
    ]
    monkeypatch.setattr(
        "app.api.routes.tenders.store.load",
        lambda: {
            "tenders": rows,
            "count": len(rows),
            "scraped_at": _day(-1),
            "source": "test",
        },
    )


def _references(payload: dict[str, Any]) -> list[str]:
    return [tender["reference"] for tender in payload["tenders"]]


def test_closed_tenders_are_hidden_by_default(listing: None) -> None:
    body = TestClient(app).get("/api/tenders").json()

    assert "EXPIRED-1" not in _references(body)
    assert "EXPIRED-2" not in _references(body)
    assert "LIVE-1" in _references(body)


def test_closed_tenders_can_be_asked_for(listing: None) -> None:
    body = TestClient(app).get("/api/tenders", params={"include_closed": True}).json()

    assert "EXPIRED-1" in _references(body)
    assert "LIVE-2" in _references(body)


def test_a_tender_with_no_deadline_is_never_hidden(listing: None) -> None:
    # A missing closing date means unknown, not past; dropping it would lose a
    # live tender on the strength of a field the portal simply did not give.
    assert "NO-DEADLINE" in _references(TestClient(app).get("/api/tenders").json())


def test_the_soonest_live_deadline_comes_first(listing: None) -> None:
    references = _references(TestClient(app).get("/api/tenders").json())

    assert references[0] == "LIVE-1"


def test_the_total_still_counts_everything_scraped(listing: None) -> None:
    # The headline figure describes the corpus, not the filtered view, so the
    # interface can say "showing 3 of 5" honestly.
    body = TestClient(app).get("/api/tenders").json()

    assert body["total_before_filter"] == 5
    assert body["count"] < body["total_before_filter"]
