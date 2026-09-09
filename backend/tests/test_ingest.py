"""Tests for the document-pack ingestion bridge.

The filename classifier, zip extraction, and key/content-type helpers are
pure functions and always run. ``archive_and_record`` needs a real database
(it opens a session via ``session_scope``), so that one test is skipped
unless DATABASE_URL is actually configured — the same pattern test_captcha.py
uses for Tesseract.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.core.config import get_settings
from app.corpus.cppp import ScrapedTender
from app.corpus.ingest import (
    _guess_content_type,
    _parse_date,
    _parse_datetime,
    _storage_key,
    archive_and_record,
    classify,
    extract_members,
)
from app.db import models


def _zip_of(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("Bill_of_Quantities.pdf", models.DocumentKind.BOQ),
        ("BOQ Rev2.xlsx", models.DocumentKind.BOQ),
        ("Notice Inviting Tender.pdf", models.DocumentKind.NIT),
        ("NIT_2026.pdf", models.DocumentKind.NIT),
        ("Corrigendum-1.pdf", models.DocumentKind.CORRIGENDUM),
        ("Addendum.pdf", models.DocumentKind.CORRIGENDUM),
        ("General Conditions of Contract.pdf", models.DocumentKind.CONDITIONS),
        ("GCC.pdf", models.DocumentKind.CONDITIONS),
        ("Site Layout Drawing.dwg", models.DocumentKind.DRAWINGS),
        ("random_annexure_7.pdf", models.DocumentKind.OTHER),
    ],
)
def test_classify_matches_filename_keywords(filename: str, expected: models.DocumentKind) -> None:
    assert classify(filename) is expected


def test_classify_checks_corrigendum_before_boq_when_both_present() -> None:
    # A corrigendum that specifically revises the BOQ should still be filed
    # as a corrigendum — that is the more specific, more actionable fact.
    assert classify("BOQ_Corrigendum_1.pdf") is models.DocumentKind.CORRIGENDUM


def test_extract_members_reads_every_file_and_drops_directories() -> None:
    payload = _zip_of(
        {
            "NIT.pdf": b"nit-bytes",
            "folder/BOQ.pdf": b"boq-bytes",
        }
    )

    members = extract_members(payload)

    names = {name for name, _ in members}
    assert names == {"NIT.pdf", "BOQ.pdf"}  # directory component stripped


def test_extract_members_empty_zip_yields_no_members() -> None:
    payload = _zip_of({})
    assert extract_members(payload) == []


def test_storage_key_sanitises_label_and_filename() -> None:
    key = _storage_key("2026/NHAI 290394", "Bill of Quantities (final).pdf")
    assert key == "cppp/2026_NHAI_290394/Bill_of_Quantities_final_.pdf"


def test_guess_content_type_falls_back_to_octet_stream() -> None:
    assert _guess_content_type("report.pdf") == "application/pdf"
    assert _guess_content_type("archive.unknownext") == "application/octet-stream"


def test_parse_date_and_datetime_accept_iso_and_reject_junk() -> None:
    assert _parse_date("2026-09-07T20:41:00").isoformat() == "2026-09-07"
    assert _parse_date(None) is None
    assert _parse_date("not-a-date") is None

    assert _parse_datetime("2026-09-21T14:00:00").hour == 14
    assert _parse_datetime(None) is None
    assert _parse_datetime("garbage") is None


class _FakeStorage:
    """In-memory DocumentStorage, so the DB test does not also need Supabase."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        self.objects[key] = data
        return key

    async def get(self, key: str) -> bytes:
        return self.objects[key]

    async def exists(self, key: str) -> bool:
        return key in self.objects

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def url_for(self, key: str, *, expires_seconds: int = 3600) -> str:
        return f"memory://{key}"


def _database_available() -> bool:
    return get_settings().database_url is not None


@pytest.mark.skipif(not _database_available(), reason="DATABASE_URL is not configured")
async def test_archive_and_record_upserts_tender_document_and_version() -> None:
    from sqlalchemy import select

    from app.db.session import session_scope

    tender = ScrapedTender(
        reference=f"TEST_INGEST_{id(object())}",
        tender_id="T-INGEST-1",
        title="Test bridge construction",
        organisation="Test Authority",
        published_at="2026-09-01T00:00:00",
        closing_at="2026-09-30T00:00:00",
        opening_at=None,
        corrigendum_count=0,
        work_category="Bridges",
        is_construction=True,
        source_listing="high_value",
        scraped_at="2026-09-01T00:00:00",
    )
    payload = _zip_of({"NIT.pdf": b"nit-bytes", "BOQ.pdf": b"boq-bytes"})
    storage = _FakeStorage()

    try:
        keys = await archive_and_record(tender, payload, storage=storage)
        assert len(keys) == 2
        assert storage.objects  # both files actually uploaded

        with session_scope() as session:
            db_tender = session.scalar(
                select(models.Tender).where(models.Tender.reference_number == tender.reference)
            )
            assert db_tender is not None
            assert db_tender.title == "Test bridge construction"
            assert db_tender.ingest_status == models.IngestStatus.PENDING
            assert len(db_tender.documents) == 2
            for document in db_tender.documents:
                assert len(document.versions) == 1
                assert document.versions[0].byte_size > 0
    finally:
        # Leave no trace: cascades take documents/versions with the tender.
        with session_scope() as session:
            db_tender = session.scalar(
                select(models.Tender).where(models.Tender.reference_number == tender.reference)
            )
            if db_tender is not None:
                session.delete(db_tender)
