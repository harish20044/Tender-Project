"""Refresh the tender listing from the public procurement portal.

    python scripts/scrape_tenders.py --pages 10 --listing high_value

Run it again to refresh. The dashboard reads whatever this last wrote.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.logging import configure_logging
from app.corpus import store
from app.corpus.cppp import LISTINGS, scrape


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listing", choices=sorted(LISTINGS), default="high_value")
    parser.add_argument(
        "--pages",
        type=int,
        default=10,
        help="Pages to read, ten rows each. The scraper waits between requests.",
    )
    parser.add_argument(
        "--construction-only",
        action="store_true",
        help="Discard tenders that are not construction work.",
    )
    parser.add_argument("--out", default=None, help="Override the output path.")
    args = parser.parse_args()

    configure_logging("INFO")

    tenders = scrape(
        args.listing,
        max_pages=args.pages,
        construction_only=args.construction_only,
    )

    if not tenders:
        print("No tenders scraped. The portal may be unreachable or its markup changed.")
        return 1

    path = store.save(tenders, args.out)

    construction = sum(1 for tender in tenders if tender.is_construction)
    print(f"\nScraped {len(tenders)} tenders from {args.listing} -> {path}")
    print(f"  construction work : {construction}")
    print(f"  other             : {len(tenders) - construction}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
