"""Background tasks: periodic tender scraping, and document downloads.

``scrape_tenders_task`` runs on a timer (see celery_app.py's beat_schedule)
independent of dashboard traffic, so refreshing the page never itself
triggers a hit against the live portal. When it finds tenders that were not
in the table before, and ``AUTO_DOWNLOAD_DOCUMENTS`` is enabled, it queues
``download_tender_documents_task`` for each one.

Kept off by default (see Settings.auto_download_documents) until a manual
``python scripts/download_documents.py`` run has confirmed the CAPTCHA gate's
selectors actually match the live portal's current markup — see documents.py.
"""

from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.core.logging import get_logger
from app.corpus import ingest, store
from app.corpus.captcha import configure_tesseract, tesseract_available
from app.corpus.cppp import scrape
from app.storage import StorageError, get_storage
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="app.workers.scrape_tenders")  # type: ignore[untyped-decorator]
def scrape_tenders_task(listing: str = "high_value", max_pages: int = 10) -> dict[str, object]:
    tenders = scrape(listing, max_pages=max_pages)
    if not tenders:
        logger.warning("scrape_task_empty", listing=listing)
        return {"count": 0, "new": 0, "updated": 0}

    result = store.save(tenders)
    logger.info("scrape_task_complete", listing=listing, **result)

    settings = get_settings()
    new_references: list[str] = result.get("new_references", [])  # type: ignore[assignment]
    if settings.auto_download_documents and new_references:
        for reference in new_references:
            download_tender_documents_task.delay(reference)
        logger.info("auto_download_queued", count=len(new_references))

    return result


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.workers.download_tender_documents",
    autoretry_for=(Exception,),
    max_retries=1,
    retry_backoff=60,
)
def download_tender_documents_task(reference: str) -> dict[str, object]:
    """Download and archive one tender's documents, by reference number.

    Runs a real Chrome and Tesseract, so the worker process this executes in
    needs Selenium, the scraper extra, and a Chrome binary available — not
    guaranteed just because a Celery worker is running (see RUNNING.md's note
    that the document downloader runs on the host, not the API/worker
    container, which does not have Chrome installed).
    """
    settings = get_settings()

    configure_tesseract()
    if not tesseract_available():
        logger.error("download_task_no_tesseract", reference=reference)
        return {"reference": reference, "status": "tesseract_unavailable"}

    data = store.load()
    row = next((r for r in data.get("tenders", []) if r.get("reference") == reference), None)
    if row is None:
        logger.warning("download_task_reference_missing", reference=reference)
        return {"reference": reference, "status": "not_found"}

    from app.corpus.documents import tender_from_row

    tender = tender_from_row(row)

    try:
        storage = get_storage()
    except StorageError as exc:
        logger.error("download_task_storage_unavailable", reference=reference, error=str(exc))
        return {"reference": reference, "status": "storage_unavailable"}

    outcome = asyncio.run(
        ingest.download_and_archive_one(
            tender,
            storage=storage,
            download_dir=settings.downloads_path,
            headless=settings.selenium_headless,
            max_captcha_attempts=settings.captcha_max_attempts,
        )
    )

    logger.info(
        "download_task_complete",
        reference=reference,
        status=outcome.download.status,
        files=len(outcome.file_keys),
    )
    return {
        "reference": reference,
        "status": outcome.download.status,
        "files": len(outcome.file_keys),
    }
