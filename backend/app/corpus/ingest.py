"""Bridges a downloaded tender document pack into the database.

The browser downloader (``documents.py``) and the CAPTCHA reader only produce
a ZIP on disk, archived whole. Nothing about a ``Tender``, ``Document``, or
``DocumentVersion`` row exists until this module runs: it unzips the pack,
guesses each member's :class:`~app.db.models.DocumentKind` from its filename,
uploads each file to the configured storage backend individually (rather than
the archive as a whole, since that is the unit the parsing pipeline will read
later), and upserts the corresponding rows.

Classification is a filename heuristic, not a read of the file's contents —
good enough to route a pack into the right buckets for a human glancing at the
archive, but not a substitute for the (still unbuilt) parsing pipeline, which
can correct ``kind`` once it actually reads each document.
"""

from __future__ import annotations

import mimetypes
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.corpus import store
from app.corpus.cppp import ScrapedTender
from app.db import models
from app.db.session import session_scope
from app.storage import DocumentStorage, content_hash

if TYPE_CHECKING:
    from app.corpus.documents import DownloadResult, TenderDocumentDownloader

logger = get_logger(__name__)

# Order matters: checked top to bottom, first match wins. Keywords are matched
# against the filename with spaces and punctuation stripped, so "Bill of
# Quantities.pdf" and "bill_of_quantities.pdf" both match "billofquantities".
_KIND_PATTERNS: tuple[tuple[models.DocumentKind, tuple[str, ...]], ...] = (
    (models.DocumentKind.CORRIGENDUM, ("corrigendum", "corrigenda", "addendum")),
    (models.DocumentKind.BOQ, ("boq", "billofquantities", "priceschedule")),
    (
        models.DocumentKind.CONDITIONS,
        (
            "gcc",
            "scc",
            "generalconditions",
            "specialconditions",
            "termsandconditions",
            "eligibility",
        ),
    ),
    (models.DocumentKind.DRAWINGS, ("drawing", "dwg", "layout", "gad")),
    (models.DocumentKind.NIT, ("nit", "noticeinvitingtender", "tendernotice")),
)


def classify(filename: str) -> models.DocumentKind:
    """Best-effort file kind from its name; the parser can correct this later."""
    normalised = re.sub(r"[^a-z0-9]", "", filename.lower())
    for kind, needles in _KIND_PATTERNS:
        if any(needle in normalised for needle in needles):
            return kind
    return models.DocumentKind.OTHER


def extract_members(zip_bytes: bytes) -> list[tuple[str, bytes]]:
    """Every regular file inside the archive, as ``(filename, data)`` pairs.

    Only the base filename is kept — CPPP packs are flat, and trusting a
    member's directory components would let a crafted archive write outside
    the intended storage prefix.
    """
    files: list[tuple[str, bytes]] = []
    with zipfile.ZipFile(BytesIO(zip_bytes)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
            if not name:
                continue
            files.append((name, archive.read(info)))
    return files


def _storage_key(tender_label: str, filename: str) -> str:
    safe_label = re.sub(r"[^A-Za-z0-9._-]+", "_", tender_label).strip("._") or "tender"
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._") or "file"
    return f"cppp/{safe_label}/{safe_name}"


def _archive_key(tender_label: str) -> str:
    """Where the whole downloaded pack lives, distinct from the per-file keys
    ``_storage_key`` produces: ``cppp/<label>.zip``."""
    safe_label = re.sub(r"[^A-Za-z0-9._-]+", "_", tender_label).strip("._") or "tender"
    return f"cppp/{safe_label}.zip"


def _guess_content_type(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


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


def _get_or_create_tender(session: Session, tender: ScrapedTender) -> models.Tender:
    """Upsert by reference number, refreshing the descriptive fields.

    A tender can be re-scraped (a corrigendum changes its closing date, for
    instance), so an existing row is updated rather than left stale — only
    ``ingest_status`` is left alone, since that belongs to whatever stage the
    pipeline has actually reached.
    """
    existing = session.scalar(
        select(models.Tender).where(models.Tender.reference_number == tender.reference)
    )
    if existing is None:
        existing = models.Tender(reference_number=tender.reference)
        session.add(existing)

    existing.title = tender.title
    existing.issuing_authority = tender.organisation
    existing.work_category = tender.work_category
    existing.published_date = _parse_date(tender.published_at)
    existing.closing_date = _parse_datetime(tender.closing_at)
    session.flush()
    return existing


def _get_or_create_document(
    session: Session, tender: models.Tender, filename: str, kind: models.DocumentKind
) -> models.Document:
    existing = session.scalar(
        select(models.Document).where(
            models.Document.tender_id == tender.id, models.Document.filename == filename
        )
    )
    if existing is not None:
        return existing

    document = models.Document(tender_id=tender.id, kind=kind, filename=filename)
    session.add(document)
    session.flush()
    return document


def _add_version(
    session: Session, document: models.Document, *, key: str, data: bytes
) -> models.DocumentVersion:
    digest = content_hash(data)
    existing = session.scalar(
        select(models.DocumentVersion).where(
            models.DocumentVersion.document_id == document.id,
            models.DocumentVersion.content_hash == digest,
        )
    )
    if existing is not None:
        # Identical bytes already recorded — a re-download, not a corrigendum.
        return existing

    next_version = (
        session.scalar(
            select(models.DocumentVersion.version)
            .where(models.DocumentVersion.document_id == document.id)
            .order_by(models.DocumentVersion.version.desc())
        )
        or 0
    ) + 1
    version = models.DocumentVersion(
        document_id=document.id,
        version=next_version,
        s3_key=key,
        content_hash=digest,
        byte_size=len(data),
        required_ocr=False,
    )
    session.add(version)
    session.flush()
    return version


async def archive_and_record(
    tender: ScrapedTender, zip_bytes: bytes, *, storage: DocumentStorage
) -> list[str]:
    """Unzip ``zip_bytes``, upload each member, and upsert its DB rows.

    Returns the storage keys written. A pack that is not actually a ZIP (some
    tenders serve a single PDF from the same link) is archived as one
    "other"-kind document rather than dropped.
    """
    label = tender.tender_id or tender.reference
    try:
        members = extract_members(zip_bytes)
    except zipfile.BadZipFile:
        members = []
    if not members:
        members = [(f"{label}.pdf", zip_bytes)]

    keys: list[str] = []
    with session_scope() as session:
        db_tender = _get_or_create_tender(session, tender)

        for filename, data in members:
            key = _storage_key(label, filename)
            await storage.put(key, data, content_type=_guess_content_type(filename))
            keys.append(key)

            kind = classify(filename)
            document = _get_or_create_document(session, db_tender, filename, kind)
            _add_version(session, document, key=key, data=data)

        db_tender.ingest_status = models.IngestStatus.PENDING

    logger.info("tender_documents_recorded", reference=tender.reference, files=len(keys))
    return keys


@dataclass(frozen=True)
class ArchiveOutcome:
    """One tender's full pipeline result: browser download, then archive."""

    download: DownloadResult
    archive_url: str | None = None
    file_keys: list[str] = field(default_factory=list)


async def archive_downloaded(
    tender: ScrapedTender, downloader: TenderDocumentDownloader, *, storage: DocumentStorage
) -> ArchiveOutcome:
    """Drive ``downloader`` for one tender, then archive and record what it got.

    Takes an already-open :class:`TenderDocumentDownloader` rather than
    owning the browser itself, so a caller working through a batch (the CLI
    script) can reuse one Chrome session across many tenders instead of
    paying startup cost per tender. ``download_and_archive_one`` below is the
    one-tender convenience wrapper for callers that do not have a batch.
    """
    result = downloader.download(tender)
    if result.status != "downloaded" or result.zip_path is None:
        return ArchiveOutcome(download=result)

    label = tender.tender_id or tender.reference
    data = result.zip_path.read_bytes()
    key = _archive_key(label)
    await storage.put(key, data, content_type="application/zip")
    url = await storage.url_for(key)
    store.update_tender_document(tender.reference, document_key=key, document_url=url)

    file_keys = await archive_and_record(tender, data, storage=storage)
    return ArchiveOutcome(download=result, archive_url=url, file_keys=file_keys)


async def download_and_archive_one(
    tender: ScrapedTender,
    *,
    storage: DocumentStorage,
    download_dir: Path | str,
    headless: bool = True,
    max_captcha_attempts: int = 8,
    debug_dir: Path | str | None = None,
) -> ArchiveOutcome:
    """The full pipeline for a single tender, owning its own browser session.

    What the Celery task uses: each queued tender gets its own Chrome
    instance, started and closed within one task, rather than sharing state
    across queued jobs.

    Selenium is imported here rather than at module level so the rest of this
    module (the pure classify/extract helpers) stays importable without it.
    """
    from app.corpus.documents import TenderDocumentDownloader

    with TenderDocumentDownloader(
        download_dir,
        headless=headless,
        max_captcha_attempts=max_captcha_attempts,
        debug_dir=debug_dir,
    ) as downloader:
        return await archive_downloaded(tender, downloader, storage=storage)
