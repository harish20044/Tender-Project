"""Read award-of-contract records and put outcomes into the corpus.

    python scripts/scrape_awards.py --year 2026 --limit 10
    python scripts/scrape_awards.py --keyword "bridge" --limit 5
    python scripts/scrape_awards.py --coverage

Comparison against past tenders is thin without outcomes: what the work was
awarded for, to whom, and against how many bidders. The notice listing cannot
carry any of that, because none of it exists until after award. CPPP
publishes it under Result of Tenders, and this reads that into
``historical_tenders``.

Slow by nature. Both the search and every detail page sit behind their own
CAPTCHA, and the reader clears one in roughly one attempt in seven, so a
record costs a dozen or more page loads. One search yields a page of links,
so the search CAPTCHA is paid once per page rather than once per record.
Budget a few minutes per record and run it as a batch, not interactively.

Needs Chrome and Tesseract on the machine. The contract value is stored as
the portal publishes it, with no currency stated — see ``app/corpus/awards``
for why nothing may present it as a checked rupee figure.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.logging import configure_logging
from app.core.tls import install_system_trust
from app.corpus.award_store import award_coverage, save_awards
from app.corpus.awards import AwardScraper


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", help="Award year, for example 2026.")
    parser.add_argument("--organisation", help="Exact organisation name as the portal lists it.")
    parser.add_argument("--keyword", help="Title keyword, for example 'bridge'.")
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="How many award records to read from the search page. Default 10.",
    )
    parser.add_argument(
        "--captcha-attempts",
        type=int,
        default=25,
        help="Attempts per CAPTCHA before giving up on a page. Default 25.",
    )
    parser.add_argument(
        "--show-browser",
        action="store_true",
        help="Run Chrome visibly, to watch what the scraper is doing.",
    )
    parser.add_argument(
        "--json",
        type=Path,
        help="Write the raw award records here as well as storing them.",
    )
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="Report how much of the corpus carries an outcome, and exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read and print the records without writing to the database.",
    )
    args = parser.parse_args()

    configure_logging("INFO")
    install_system_trust()

    if args.coverage:
        print(json.dumps(award_coverage(), indent=2))
        return 0

    with AwardScraper(
        headless=not args.show_browser,
        captcha_attempts=args.captcha_attempts,
    ) as scraper:
        records = scraper.harvest(
            year=args.year,
            organisation=args.organisation,
            keyword=args.keyword,
            limit=args.limit,
        )

    if not records:
        print("No award records were read. The CAPTCHA may have beaten us; try again.")
        return 1

    for record in records:
        value = "not stated" if record.contract_value is None else f"{record.contract_value:,.0f}"
        print(
            f"  {record.reference or '?':28} {(record.selected_bidder or '-')[:34]:36} "
            f"bids={record.bids_received if record.bids_received is not None else '-':>4} "
            f"value={value}"
        )

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps([r.as_dict() for r in records], indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")

    if args.dry_run:
        print("\ndry run: nothing written.")
        return 0

    summary = save_awards(records)
    print(f"\nstored: {json.dumps(summary)}")
    print(f"coverage: {json.dumps(award_coverage())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
