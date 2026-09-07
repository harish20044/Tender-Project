"""Persistence for scraped tenders.

A JSON file rather than the database, deliberately and temporarily. Postgres
with pgvector cannot be installed on a Windows machine without a compiler, and
Docker is unavailable here, so requiring a database to see the dashboard would
mean nobody can run it. The scraper writes here; the API reads here.

The interface is narrow on purpose. When a database is available, the same two
functions move to SQLAlchemy and nothing above this layer changes.
"""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.corpus.cppp import ScrapedTender

logger = get_logger(__name__)


def _resolve(path: Path | str | None) -> Path:
    """Where the cache lives.

    Taken from settings rather than the working directory, so the scraper and
    the API agree no matter where each was launched from — and so Compose can
    point both at a mounted volume, where the repository layout the default
    infers from does not exist.
    """
    if path:
        return Path(path)
    return get_settings().data_path / "cache" / "tenders.json"


def save(tenders: list[ScrapedTender], path: Path | str | None = None) -> Path:
    """Write the scrape result, replacing whatever was there.

    Written to a temporary file in the same directory and then moved, so a
    crash mid-write cannot leave the API reading half a JSON document.
    """
    target = _resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "scraped_at": datetime.now(UTC).isoformat(),
        "source": "eprocure.gov.in/cppp",
        "count": len(tenders),
        "tenders": [tender.as_dict() for tender in tenders],
    }

    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=target.parent, delete=False, suffix=".partial"
    ) as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        temp_path = Path(handle.name)

    temp_path.replace(target)
    logger.info("tenders_saved", path=str(target), count=len(tenders))
    return target


def load(path: Path | str | None = None) -> dict[str, Any]:
    """Read the last scrape.

    Returns an empty result rather than raising when nothing has been scraped
    yet, so the API can report "no data" instead of failing.
    """
    source = _resolve(path)

    if not source.is_file():
        return {"scraped_at": None, "source": None, "count": 0, "tenders": []}

    try:
        with source.open(encoding="utf-8") as handle:
            data: dict[str, Any] = json.load(handle)
        return data
    except json.JSONDecodeError as exc:
        logger.error("tender_cache_corrupt", path=str(source), error=str(exc))
        return {"scraped_at": None, "source": None, "count": 0, "tenders": []}
