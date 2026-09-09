"""Download tender document packs and archive them in Supabase.

    python scripts/download_documents.py --limit 5
    python scripts/download_documents.py --reference 2026_NHAI_290394_1 --headed
    python scripts/download_documents.py --local-only      # keep ZIPs on disk

Reads the last scrape (data/cache/tenders.json), opens each tender's detail
page in a real Chrome, answers the CAPTCHA gate by OCR, saves the ZIP under
data/downloads/, uploads it to Supabase Storage, and records the URL on the
tender's row in the cache — which is where the rest of the pipeline picks it
up.

Needs three things installed once (see RUNNING.md): the `scraper` extra, the
Tesseract program, and a Chrome for the driver to control.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.corpus import ingest, store
from app.corpus.captcha import configure_tesseract, tesseract_available
from app.corpus.cppp import ScrapedTender
from app.corpus.documents import (
    DownloadResult,
    TenderDocumentDownloader,
    tender_from_row,
)
from app.storage import DocumentStorage, StorageError, get_storage


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listing", help="Only tenders from this source listing.")
    parser.add_argument(
        "--reference",
        action="append",
        default=None,
        help="Only this tender (repeatable). Matches reference or tender id.",
    )
    parser.add_argument(
        "--construction-only",
        action="store_true",
        help="Skip tenders that are not construction work.",
    )
    parser.add_argument("--limit", type=int, default=10, help="At most this many downloads.")
    parser.add_argument("--headed", action="store_true", help="Show the browser while it works.")
    parser.add_argument(
        "--max-captcha-attempts",
        type=int,
        default=None,
        help="OCR attempts per tender (default: CAPTCHA_MAX_ATTEMPTS, itself 8).",
    )
    parser.add_argument(
        "--search-pages",
        type=int,
        default=5,
        help="Listing pages to walk for rows with no detail URL recorded.",
    )
    parser.add_argument(
        "--redownload",
        action="store_true",
        help="Fetch again even for tenders already archived in this cache.",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Skip the Supabase upload; the ZIP stays in the downloads folder.",
    )
    parser.add_argument("--download-dir", default=None, help="Override the downloads folder.")
    parser.add_argument(
        "--debug-dir",
        default=None,
        help="Save every CAPTCHA image and its read, for tuning the OCR.",
    )
    parser.add_argument("--tesseract", default=None, help="Path to the tesseract program.")
    return parser.parse_args()


def _select_rows(args: argparse.Namespace) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = list(store.load().get("tenders", []))

    if args.reference:
        wanted = {value.strip().lower() for value in args.reference}
        rows = [
            row
            for row in rows
            if str(row.get("reference", "")).lower() in wanted
            or str(row.get("tender_id") or "").lower() in wanted
        ]
    if args.listing:
        rows = [row for row in rows if row.get("source_listing") == args.listing]
    if args.construction_only:
        rows = [row for row in rows if row.get("is_construction")]
    if not args.redownload:
        rows = [row for row in rows if not row.get("document_key")]

    return rows[: max(0, args.limit)]


def _storage_key(tender: ScrapedTender) -> str:
    """Where the pack lives in the bucket: cppp/<tender id>.zip."""
    label = tender.tender_id or tender.reference
    label = re.sub(r"[^A-Za-z0-9._-]+", "_", label).strip("._") or "tender"
    return f"cppp/{label}.zip"


def _build_uploader(args: argparse.Namespace) -> DocumentStorage | None:
    if args.local_only:
        return None
    try:
        return get_storage()
    except StorageError as exc:
        raise SystemExit(
            f"{exc} (create the bucket in the Supabase dashboard first), or pass "
            "--local-only to keep the ZIPs on disk."
        ) from exc


def _archive_and_ingest(
    storage: DocumentStorage, tender: ScrapedTender, key: str, data: bytes
) -> tuple[str, list[str]]:
    """Upload the whole pack under ``key`` (the pointer the JSON cache keeps),
    then unzip it and upsert Tender/Document/DocumentVersion rows for every
    file inside — the shape the rest of the pipeline actually reads."""

    async def _run() -> tuple[str, list[str]]:
        await storage.put(key, data, content_type="application/zip")
        url = await storage.url_for(key)
        file_keys = await ingest.archive_and_record(tender, data, storage=storage)
        return url, file_keys

    return asyncio.run(_run())


def main() -> int:
    args = _parse_args()
    configure_logging("INFO")

    configure_tesseract(args.tesseract)
    if not tesseract_available():
        print(
            "Tesseract was not found. Install it (Windows build: "
            "https://github.com/UB-Mannheim/tesseract/wiki) and put it on PATH, "
            "or set TESSERACT_CMD in .env to its full path."
        )
        return 2

    rows = _select_rows(args)
    if not rows:
        print(
            "Nothing to download: scrape first (scripts/scrape_tenders.py), or every "
            "candidate is already archived. --redownload forces a refetch."
        )
        return 0

    print(f"{len(rows)} tender(s) to fetch.")
    uploader = _build_uploader(args)
    settings = get_settings()
    download_dir = Path(args.download_dir) if args.download_dir else settings.downloads_path

    stored = failed = 0
    with TenderDocumentDownloader(
        download_dir,
        headless=not args.headed,
        max_captcha_attempts=args.max_captcha_attempts or settings.captcha_max_attempts,
        listing_search_pages=args.search_pages,
        debug_dir=args.debug_dir,
    ) as downloader:
        for index, row in enumerate(rows, start=1):
            tender = tender_from_row(row)
            print(f"\n[{index}/{len(rows)}] {tender.reference} — {tender.title[:70]}")

            result: DownloadResult = downloader.download(tender)
            if result.status != "downloaded" or result.zip_path is None:
                failed += 1
                print(f"  {result.status}: {result.error or 'no archive produced'}")
                time.sleep(settings.download_settle_seconds)
                continue

            key = _storage_key(tender)
            data = result.zip_path.read_bytes()
            if uploader is not None:
                url, file_keys = _archive_and_ingest(uploader, tender, key, data)
                store.update_tender_document(tender.reference, document_key=key, document_url=url)
                print(f"  saved {result.zip_path.name} ({len(data):,} bytes)")
                print(f"  supabase -> {key}  {url}")
                print(f"  db -> {len(file_keys)} document(s) recorded")
            else:
                print(f"  kept locally -> {result.zip_path} ({len(data):,} bytes)")
            stored += 1
            time.sleep(settings.download_settle_seconds)

    print(f"\nDone. archived: {stored}, failed: {failed}.")
    return 0 if stored else 1


if __name__ == "__main__":
    raise SystemExit(main())
