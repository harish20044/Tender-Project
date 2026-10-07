"""Score extraction and retrieval against the golden set.

    python scripts/evaluate.py
    python scripts/evaluate.py --json runs/2026-10-07.json
    python scripts/evaluate.py --case nhai-bypass --skip-abstention

Runs each golden tender through the real ingest, retrieval and extraction
path, then compares what came back against answers that are known rather
than assumed. Reports three rates that fail for different reasons: whether
the value is right, whether the citation points at the page the value is
actually on, and whether a question the document cannot answer is declined
instead of invented.

Needs the database and both provider keys, and spends real tokens — roughly
a cent a case. It is a script and not a test for exactly that reason.

Exits non-zero if any case falls below the thresholds, so it can gate a
release without anyone having to read the table.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.logging import configure_logging
from app.core.tls import install_system_trust
from app.eval import CASES_BY_KEY, as_json, render, run_all_sync


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case",
        action="append",
        choices=sorted(CASES_BY_KEY),
        help="Score only this case. Repeatable. Defaults to all of them.",
    )
    parser.add_argument(
        "--json",
        type=Path,
        help="Also write the full per-fact result here, for tracking runs over time.",
    )
    parser.add_argument(
        "--skip-abstention",
        action="store_true",
        help="Skip the unanswerable questions. Faster, and cheaper in tokens.",
    )
    parser.add_argument(
        "--min-accuracy",
        type=float,
        default=0.9,
        help="Fail below this value accuracy. Default 0.9.",
    )
    parser.add_argument(
        "--min-page-accuracy",
        type=float,
        default=0.9,
        help="Fail below this page-citation accuracy. Default 0.9.",
    )
    args = parser.parse_args()

    configure_logging("WARNING")  # the table is the output; logs would bury it
    install_system_trust()

    results = run_all_sync(args.case, check_abstention=not args.skip_abstention)
    print(render(results))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(as_json(results), encoding="utf-8")
        print(f"\nwrote {args.json}")

    failures = [
        f"{r.case_key}: value accuracy {r.extraction.accuracy:.1%} below {args.min_accuracy:.0%}"
        for r in results
        if r.extraction.accuracy < args.min_accuracy
    ] + [
        f"{r.case_key}: page accuracy {r.extraction.page_accuracy:.1%} "
        f"below {args.min_page_accuracy:.0%}"
        for r in results
        if r.extraction.page_accuracy < args.min_page_accuracy
    ]

    if failures:
        print("\nbelow threshold:")
        for failure in failures:
            print(f"  {failure}")
        return 1

    print("\nall cases met the thresholds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
