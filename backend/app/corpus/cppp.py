"""Scraper for the Central Public Procurement Portal.

Source: https://eprocure.gov.in/cppp/ — the Government of India portal that
aggregates tender notices from central ministries, public sector undertakings
and state departments.

On access. The portal publishes no robots.txt, so no crawl directive is
stated. These listings exist to be read by prospective bidders, which is the
use here. The scraper still behaves conservatively: it identifies itself, waits
between requests, caps how many pages it will take in one run, and never
touches the captcha-protected search form. It reads the same public listing a
browser would.

What the listing does and does not carry. Each row gives the reference, title,
organisation and the three key dates. It does not give the contract value, so
that field stays null rather than being invented; values appear only inside
the tender documents themselves, which is a later stage of the pipeline.
"""

from __future__ import annotations

import base64
import html
import re
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from app.core.logging import get_logger

logger = get_logger(__name__)

BASE = "https://eprocure.gov.in/cppp"

# The portal's own listing views. "High value" is the useful one for
# construction work; "latest active" is broader and noisier.
LISTINGS = {
    "high_value": f"{BASE}/highvaluetenders/cpppdata",
    "latest_active": f"{BASE}/latestactivetendersnew/cpppdata",
}

USER_AGENT = (
    "TenderIntelligenceBot/0.1 (academic research project; "
    "contact via repository github.com/harish20044/Tender-Project)"
)

# One request every this many seconds. The portal is public infrastructure and
# there is no reason to hurry it.
REQUEST_DELAY_SECONDS = 1.5
REQUEST_TIMEOUT_SECONDS = 40

DATE_FORMAT = "%d-%b-%Y %I:%M %p"

# Subject-matter vocabulary. Matched on word boundaries, because substring
# matching produces nonsense: "track" hits "TRACKED EXCAVATOR", and "rail"
# hits "guardrail".
CONSTRUCTION_TERMS = {
    "Roads & Highways": (
        "road",
        "roads",
        "highway",
        "highways",
        "four-laning",
        "four-lane",
        "six-lane",
        "bypass",
        "carriageway",
        "flyover",
        "culvert",
        "pavement",
    ),
    "Buildings": (
        "building",
        "buildings",
        "quarters",
        "accommodation",
        "hostel",
        "school",
        "hospital",
        "renovation",
        "civil works",
    ),
    "Bridges": ("bridge", "bridges", "viaduct", "underpass", "overbridge"),
    "Water Resources": (
        "canal",
        "dam",
        "embankment",
        "irrigation",
        "drainage",
        "sewerage",
        "reservoir",
        "aqueduct",
    ),
    "Railways": ("railway", "railways", "gauge", "siding", "sleepers"),
    "Power": ("substation", "transmission", "powerhouse", "electrification"),
}

# A tender only counts as construction if it commissions work, not if it
# merely mentions a structure. "Cleaning of weigh bridges" and "supply of
# cement" both name construction nouns without being construction contracts.
WORK_VERBS = (
    "construction",
    "constructing",
    "erection",
    "widening",
    "strengthening",
    "laning",
    "upgradation",
    "improvement",
    "rehabilitation",
    "renovation",
    "repair",
    "repairs",
    "development",
    "execution",
    "civil work",
    "civil works",
    "epc",
    "building work",
)
# "installation" is deliberately absent: it describes equipment far more often
# than structures, and it was pulling in rack power systems and IT fit-outs.

# Procurement of goods or services, even where a structure is named.
NON_WORK_MARKERS = (
    "cleaning",
    "housekeeping",
    "security service",
    "manpower",
    "hiring",
    "engagement of",
    "appointment of",
    "insurance",
    "audit",
    "consultancy",
    "supply of",
    "procurement of",
    "purchase of",
    "annual maintenance",
    "amc",
    "transportation of",
    "disposal of",
    "auction",
    "scanning",
    "printing",
    "catering",
    "user fee",
)


def _has_word(text: str, terms: tuple[str, ...]) -> bool:
    """Word-boundary match, so a term never fires inside a longer word."""
    return any(re.search(rf"\b{re.escape(term)}\b", text) for term in terms)


@dataclass(frozen=True)
class ScrapedTender:
    """One row of a portal listing, normalised."""

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
    # The title cell links to the tender's detail view. The href is a
    # self-contained token with no session state in it, which is what lets the
    # document downloader open the same tender in a browser later.
    detail_url: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #


def _page_url(listing_url: str, page: int) -> str:
    """Build a paginated URL.

    The portal takes the real target as a base64-encoded ``url`` parameter
    rather than a plain page number, so page two is reached by encoding
    ``...?page=2`` and passing it along.
    """
    if page <= 1:
        return listing_url

    target = f"{listing_url}?page={page}"
    encoded = urllib.parse.quote(base64.b64encode(target.encode()).decode(), safe="")
    return f"{listing_url}?url={encoded}"


def _fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        body: str = response.read().decode(charset, errors="replace")
    return body


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

_TAG = re.compile(r"<[^>]+>")
_ROW = re.compile(r"<tr.*?</tr>", re.S | re.I)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_ANCHOR = re.compile(r"<a[^>]+href=\"([^\"]+)\"", re.I)


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", fragment))).strip()


def _row_cells(row_html: str) -> list[str]:
    return [_clean(cell) for cell in _CELL.findall(row_html)]


def _detail_url(row_html: str) -> str | None:
    """The detail-page link from a row's title cell, if it has one.

    Data rows carry exactly one anchor, on the title; taking the anchor from
    the title cell (the fifth) rather than the first in the row keeps the
    extraction honest should the portal ever link something else.
    """
    cells = _CELL.findall(row_html)
    if len(cells) < 5:
        return None
    match = _ANCHOR.search(cells[4])
    if not match:
        return None
    return html.unescape(match.group(1)).strip() or None


def _parse_datetime(value: str) -> str | None:
    """Portal timestamps look like ``07-Sep-2026 08:41 PM``."""
    try:
        return datetime.strptime(value.strip(), DATE_FORMAT).isoformat()
    except ValueError:
        return None


def _split_title(raw: str) -> tuple[str, str, str | None]:
    """Separate the combined Title/Ref.No./Tender Id cell.

    The portal joins all three with slashes and does not escape slashes inside
    the title, so the split works from the right: the last segment is the
    tender id when numeric, and the one before it is the reference.
    """
    parts = [segment.strip() for segment in raw.split("/") if segment.strip()]
    if not parts:
        return raw.strip(), raw.strip(), None

    tender_id = parts[-1] if parts[-1].isdigit() else None
    reference = parts[-2] if tender_id and len(parts) >= 2 else parts[-1]
    title_parts = parts[: -2 if tender_id and len(parts) >= 2 else -1]
    title = "/".join(title_parts).strip() or reference

    return title, reference, tender_id


def _parse_corrigendum(value: str) -> int:
    digits = re.findall(r"\d+", value)
    return int(digits[0]) if digits else 0


def _classify(title: str) -> tuple[str | None, bool]:
    """Derive a work category from the title, and say whether it is real work.

    Two conditions, because either alone is wrong. Subject vocabulary alone
    calls "cleaning of weigh bridges" a bridge contract. A work verb alone
    calls "supply and installation of servers" construction. A tender must
    name a structure *and* commission work on it, and must not be an obvious
    goods or services purchase.

    Keyword rules rather than a model: the vocabulary is small and stable, and
    a wrong call here is visible on the dashboard where it can be corrected.
    """
    lowered = title.lower()

    if _has_word(lowered, NON_WORK_MARKERS):
        return None, False

    if not _has_word(lowered, WORK_VERBS):
        return None, False

    # Scored rather than first-match-wins. A title naming both a building and
    # a viaduct should land in the category it mentions more, not in whichever
    # happens to be checked first.
    scores = {
        category: sum(1 for term in terms if re.search(rf"\b{re.escape(term)}\b", lowered))
        for category, terms in CONSTRUCTION_TERMS.items()
    }
    best = max(scores, key=lambda category: scores[category])

    if scores[best]:
        return best, True

    # Work is being commissioned but the structure is unrecognised. Still
    # construction, just uncategorised — better than silently dropping it.
    return "General Civil", True


def _parse_listing(page_html: str, listing_name: str) -> list[ScrapedTender]:
    scraped_at = datetime.now(UTC).isoformat()
    tenders: list[ScrapedTender] = []

    for row_html in _ROW.findall(page_html):
        cells = _row_cells(row_html)

        # Data rows have seven cells and open with a serial number.
        if len(cells) != 7 or not cells[0].rstrip(".").isdigit():
            continue

        _, published, closing, opening, title_cell, organisation, corrigendum = cells
        title, reference, tender_id = _split_title(title_cell)
        category, is_construction = _classify(title)

        tenders.append(
            ScrapedTender(
                reference=reference,
                tender_id=tender_id,
                title=title,
                organisation=organisation,
                published_at=_parse_datetime(published),
                closing_at=_parse_datetime(closing),
                opening_at=_parse_datetime(opening),
                corrigendum_count=_parse_corrigendum(corrigendum),
                work_category=category,
                is_construction=is_construction,
                source_listing=listing_name,
                scraped_at=scraped_at,
                detail_url=_detail_url(row_html),
            )
        )

    return tenders


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def scrape(
    listing: str = "high_value",
    *,
    max_pages: int = 5,
    construction_only: bool = False,
) -> list[ScrapedTender]:
    """Read a portal listing, following pagination.

    Stops early when a page yields no rows, which is how the portal signals the
    end rather than returning a 404.
    """
    if listing not in LISTINGS:
        raise ValueError(f"Unknown listing {listing!r}. Choose from {sorted(LISTINGS)}.")

    listing_url = LISTINGS[listing]
    collected: list[ScrapedTender] = []
    seen: set[str] = set()

    for page in range(1, max_pages + 1):
        if page > 1:
            time.sleep(REQUEST_DELAY_SECONDS)

        url = _page_url(listing_url, page)
        try:
            body = _fetch(url)
        except Exception as exc:
            # A failed page should not lose the pages already gathered.
            logger.warning("cppp_page_failed", listing=listing, page=page, error=str(exc))
            break

        rows = _parse_listing(body, listing)
        if not rows:
            logger.info("cppp_no_more_rows", listing=listing, page=page)
            break

        new = [row for row in rows if row.reference not in seen]
        seen.update(row.reference for row in new)
        collected.extend(new)

        logger.info("cppp_page_scraped", listing=listing, page=page, rows=len(new))

    if construction_only:
        collected = [tender for tender in collected if tender.is_construction]

    return collected
