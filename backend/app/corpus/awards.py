"""Award-of-contract records from the CPPP "Result of Tenders" listing.

Why this exists. Comparison against past tenders is only worth much if the
past tenders carry outcomes — what the work was actually awarded for, and how
many bidders turned up. The notice listing cannot carry either, because
neither exists until after award. CPPP publishes them separately, under
Result of Tenders, and this reads that.

What a record carries. The award detail states the reference, the selected
bidder and their address, the number of bids received, the contract value,
and the contract and completion dates. That is the outcome half of the
corpus, and `HistoricalTender` already had columns waiting for it.

Two things to be honest about.

The portal prints its own disclaimer beside the contract value: "Currency
regarding Contract Value may please be checked with the corresponding tender
portals/websites." There is no unit on the figure. It is stored as published
and `value_is_unverified` is set, so a comparison can show it while making
clear it is not a checked rupee amount. Treating it as rupees silently would
be the wrong kind of confident.

Both the search and each detail page sit behind their own CAPTCHA, and the
reader clears one in roughly one attempt in seven. So a record costs a dozen
or more attempts, which is why this is a batch job and not something a
request handler calls. One search yields a page of detail links, so the
search CAPTCHA is paid once per page rather than once per record.
"""

from __future__ import annotations

import contextlib
import re
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Any

from app.core.logging import get_logger
from app.corpus.browser import session_is_dead

logger = get_logger(__name__)

BASE = "https://eprocure.gov.in"
SEARCH_URL = f"{BASE}/cppp/resultoftendersnew/cpppdata"

# The captcha image carries no id, only Drupal's selector attribute.
CAPTCHA_IMAGE = 'img[data-drupal-selector="edit-captcha-image"]'
CAPTCHA_FIELD = "captcha_response"

# The portal's own labels, verbatim. They are matched after normalisation,
# and both sides go through the same function below so the two cannot drift:
# writing the normalised form out by hand is how "bidder(s)" and
# "Completion/Completion" got mismatched in the first place.
#
# Several rows put two label/value pairs side by side, so the parser walks
# cells rather than assuming one pair per row.
_PORTAL_LABELS = {
    "Organisation Name": "organisation",
    "Tender Ref. No.": "reference",
    "Tender Description": "description",
    "Tender Type": "tender_type",
    "Number of bids received": "bids_received",
    "Name of the selected bidder(s)": "selected_bidder",
    "Contract Value": "contract_value",
    "Address of the selected bidder(s)": "bidder_address",
    "Published Date": "published_date",
    "Contract Date": "contract_date",
    "Date of Completion/Completion Period in Days": "completion_date",
}

_TAG = re.compile(r"<[^>]+>")
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_WS = re.compile(r"\s+")

# "22-Dec-2025 09:00 AM" is the portal's format throughout.
_DATE_FORMATS = ("%d-%b-%Y %I:%M %p", "%d-%b-%Y")


@dataclass(frozen=True)
class AwardRecord:
    """One award, as published."""

    reference: str | None = None
    organisation: str | None = None
    description: str | None = None
    selected_bidder: str | None = None
    bidder_address: str | None = None
    bids_received: int | None = None
    contract_value: float | None = None
    # The portal states no currency for the contract value, so nothing here
    # may present it as a checked rupee figure.
    value_is_unverified: bool = True
    published_date: str | None = None
    contract_date: str | None = None
    completion_date: str | None = None
    source_url: str | None = None
    scraped_at: str | None = None

    @property
    def is_useful(self) -> bool:
        """Whether the record adds an outcome worth storing.

        A row with a reference but no bidder, no bid count and no value is
        an award notice with nothing in it — common on rate contracts — and
        storing it would pad the corpus without informing a comparison.
        """
        return bool(self.reference) and any(
            (
                self.selected_bidder,
                self.bids_received is not None,
                self.contract_value is not None,
            )
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean(fragment: str) -> str:
    text = _TAG.sub(" ", fragment)
    for entity, replacement in (("&nbsp;", " "), ("&amp;", "&"), ("&#39;", "'"), ("&quot;", '"')):
        text = text.replace(entity, replacement)
    return _WS.sub(" ", text).strip()


def _normalise_label(text: str) -> str:
    """A label reduced to words, for matching.

    Punctuation becomes a space rather than being deleted: "/" in
    "Completion/Completion" would otherwise fuse two words into one, and
    "(s)" in "bidder(s)" would leave a stray letter either way. Collapsing
    whitespace afterwards makes both harmless.
    """
    return _WS.sub(" ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()


# Built from the portal's own labels, so both sides of the match normalise
# identically by construction.
_LABELS = {_normalise_label(label): field for label, field in _PORTAL_LABELS.items()}


def _parse_money(text: str) -> float | None:
    """The contract value as published, or None.

    Indian digit grouping and a stray currency symbol both appear, so
    separators are stripped before parsing. Zero is returned as None rather
    than as a value: the portal uses it for "not stated", and a comparison
    that treated it as a nil contract would be badly wrong.
    """
    digits = re.sub(r"[^\d.]", "", text or "")
    if not digits:
        return None
    try:
        value = float(digits)
    except ValueError:
        return None
    return value if value > 0 else None


def _parse_int(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text or "")
    if not digits:
        return None
    value = int(digits)
    # Unlike a value, zero bids received is a real and meaningful outcome.
    return value


def _parse_date(text: str) -> str | None:
    candidate = (text or "").strip()
    if not candidate:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(candidate, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_award_detail(html: str, *, source_url: str | None = None) -> AwardRecord:
    """Pull an award record out of a detail page.

    Cells are walked in document order rather than row by row: the portal
    puts "Tender Type" and "Number of bids received" in the same row, and a
    row-based parser reads the second pair's label as the first pair's value.
    """
    cells = [_clean(cell) for cell in _CELL.findall(html)]
    found: dict[str, str] = {}

    for index, cell in enumerate(cells):
        field = _LABELS.get(_normalise_label(cell))
        if not field or field in found:
            continue
        # The value is the next cell that is not the separating colon.
        for candidate in cells[index + 1 : index + 4]:
            if candidate and candidate not in {":", "*"}:
                found[field] = candidate
                break

    return AwardRecord(
        reference=found.get("reference") or None,
        organisation=found.get("organisation") or None,
        description=found.get("description") or None,
        selected_bidder=found.get("selected_bidder") or None,
        bidder_address=found.get("bidder_address") or None,
        bids_received=_parse_int(found.get("bids_received", "")),
        contract_value=_parse_money(found.get("contract_value", "")),
        published_date=_parse_date(found.get("published_date", "")),
        contract_date=_parse_date(found.get("contract_date", "")),
        completion_date=_parse_date(found.get("completion_date", "")),
        source_url=source_url,
        scraped_at=datetime.now(UTC).isoformat(),
    )


def awarded_date_of(record: AwardRecord) -> date | None:
    """The date the contract was awarded, as a date object."""
    raw = record.contract_date or record.published_date
    return date.fromisoformat(raw) if raw else None


class AwardScraper:
    """Browser-driven reader for the Result of Tenders pages.

    A browser rather than plain HTTP because both pages are session-bound:
    the detail link embeds a timestamp and is rejected outside the session
    that produced it, and a cookie-carrying fetch of the same URL returns
    the portal's "Invalid Url" page.
    """

    def __init__(
        self,
        *,
        headless: bool = True,
        captcha_attempts: int = 25,
        page_timeout: float = 60.0,
        settle_seconds: float = 4.0,
        max_session_restarts: int = 6,
    ) -> None:
        self._headless = headless
        self._captcha_attempts = captcha_attempts
        self._page_timeout = page_timeout
        self._settle = settle_seconds
        # Chrome dies every few page loads on some machines. A batch pays a
        # CAPTCHA per record, so it crosses that threshold routinely and a
        # run with no restart budget stops early with no explanation.
        self._max_session_restarts = max(0, max_session_restarts)
        self._restarts_used = 0
        self._driver: Any = None

    def __enter__(self) -> AwardScraper:
        self._driver = self._build_driver()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception as exc:  # pragma: no cover - teardown only
                logger.warning("award_driver_quit_failed", error=str(exc))
            self._driver = None

    def restart(self) -> bool:
        """Replace a dead browser, if the restart budget allows it.

        Bounded rather than unlimited: a browser that dies immediately and
        repeatedly is a broken environment, and retrying forever would hide
        that behind a run that never finishes.
        """
        if self._restarts_used >= self._max_session_restarts:
            logger.warning("award_restart_budget_spent", used=self._restarts_used)
            return False
        self._restarts_used += 1
        logger.warning("award_browser_restarting", attempt=self._restarts_used)
        if self._driver is not None:
            with contextlib.suppress(Exception):
                self._driver.quit()
            self._driver = None
        self._driver = self._build_driver()
        return True

    def _build_driver(self) -> Any:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        options = Options()
        if self._headless:
            options.add_argument("--headless=new")
        options.add_argument("--window-size=1400,1000")
        options.add_argument("--disable-blink-features=AutomationControlled")
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(self._page_timeout)
        return driver

    # -- captcha ---------------------------------------------------------- #

    def _answer_captcha(self) -> bool:
        """Read the captcha on the current page and submit the form.

        Always submits, even on an unconvincing read. A wrong answer makes
        the portal re-render the form with a fresh image, which is the only
        way to get a new one without re-fetching the page — and on a detail
        page re-fetching is exactly what must not happen. Submitting a known
        bad answer is therefore the cheap way to reroll.

        Returns False only when there is no CAPTCHA form here to submit.
        """
        from selenium.webdriver.common.by import By

        from app.corpus.captcha import solve

        images = self._driver.find_elements(By.CSS_SELECTOR, CAPTCHA_IMAGE)
        fields = self._driver.find_elements(By.NAME, CAPTCHA_FIELD)
        submits = self._driver.find_elements(By.CSS_SELECTOR, 'input[type="submit"]')
        if not (images and fields and submits):
            return False

        answer = solve(images[0].screenshot_as_png)
        if len(answer) < 4:
            # Deliberately wrong, to force a fresh image on the next render.
            answer = "0"
            logger.debug("award_captcha_unreadable")

        fields[0].clear()
        fields[0].send_keys(answer)
        submits[0].click()
        time.sleep(self._settle)
        return True

    def _captcha_still_showing(self) -> bool:
        from selenium.webdriver.common.by import By

        body = self._driver.find_element(By.TAG_NAME, "body").text.lower()
        return "what code is in the image" in body or "enter the characters" in body

    # -- search ----------------------------------------------------------- #

    def search(
        self,
        *,
        year: str | None = None,
        organisation: str | None = None,
        keyword: str | None = None,
        status: str = "Published",
    ) -> list[str]:
        """Detail-page URLs for one page of award results.

        Retries the CAPTCHA rather than failing on it. An empty list means
        the search itself returned nothing, not that the CAPTCHA beat us —
        those are distinguished in the log.
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as conditions
        from selenium.webdriver.support.ui import WebDriverWait

        for attempt in range(1, self._captcha_attempts + 1):
            # A slow page, a stale element or a dropped connection costs one
            # attempt rather than the whole batch. Over twenty-odd attempts
            # against a public portal, one of them going wrong is normal, and
            # a run that dies on it wastes every CAPTCHA already cleared.
            try:
                self._driver.get(SEARCH_URL)
                WebDriverWait(self._driver, 30).until(
                    conditions.presence_of_element_located((By.CSS_SELECTOR, CAPTCHA_IMAGE))
                )

                self._select("year", year)
                self._select("aoc_status", status)
                self._type("keyword", keyword)
                self._type("org_name", organisation)

                if not self._answer_captcha():
                    continue

                links = [
                    element.get_attribute("href")
                    for element in self._driver.find_elements(By.TAG_NAME, "a")
                    if "aocfullview" in (element.get_attribute("href") or "")
                ]
                if links:
                    logger.info("award_search_ok", attempts=attempt, results=len(links))
                    return [link for link in links if link]

                if not self._captcha_still_showing():
                    logger.info("award_search_empty", attempts=attempt)
                    return []
            except Exception as exc:
                logger.warning(
                    "award_search_attempt_failed",
                    attempt=attempt,
                    error=f"{type(exc).__name__}: {str(exc)[:120]}",
                )
                # Every further attempt against a dead browser fails the same
                # way, so retrying without replacing it just spends the budget.
                if session_is_dead(exc) and not self.restart():
                    break

        logger.warning("award_search_captcha_exhausted", attempts=self._captcha_attempts)
        return []

    def _select(self, name: str, value: str | None) -> None:
        """Choose a dropdown value, tolerating the field being absent.

        The portal's two result listings do not carry identical filters, so a
        missing field is a difference between pages rather than a failure.
        """
        if not value:
            return
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import Select

        try:
            Select(self._driver.find_element(By.NAME, name)).select_by_value(value)
        except Exception as exc:
            logger.debug("award_select_skipped", field=name, error=str(exc))

    def _type(self, name: str, value: str | None) -> None:
        if not value:
            return
        from selenium.webdriver.common.by import By

        try:
            field = self._driver.find_element(By.NAME, name)
            field.clear()
            field.send_keys(value)
        except Exception as exc:
            logger.debug("award_type_skipped", field=name, error=str(exc))

    # -- detail ----------------------------------------------------------- #

    def read_detail_here(self, *, source_url: str | None = None) -> AwardRecord | None:
        """Clear the CAPTCHA on the detail page already open, and parse it.

        Retries by resubmitting the form, never by re-fetching the URL. That
        distinction is the whole reason this takes the shape it does: a
        direct GET of a detail link returns the portal's generic page with
        neither the record nor a CAPTCHA on it, because the link is only
        honoured as part of the navigation that produced it. Submitting the
        form posts back to the same address and keeps that context, so a
        wrong answer simply re-renders with a fresh image.
        """
        for attempt in range(1, self._captcha_attempts + 1):
            if not self._captcha_still_showing():
                record = parse_award_detail(self._driver.page_source, source_url=source_url)
                if record.reference:
                    logger.info(
                        "award_detail_ok",
                        attempts=attempt,
                        reference=record.reference,
                        bidder=record.selected_bidder,
                        value=record.contract_value,
                    )
                    return record
                logger.warning("award_detail_unreadable", url=(source_url or "")[:90])
                return None

            if not self._answer_captcha():
                logger.warning("award_detail_no_form", url=(source_url or "")[:90])
                return None

        logger.warning("award_detail_captcha_exhausted", url=(source_url or "")[:90])
        return None

    def harvest(
        self,
        *,
        year: str | None = None,
        organisation: str | None = None,
        keyword: str | None = None,
        limit: int = 10,
    ) -> list[AwardRecord]:
        """Search once, then read up to ``limit`` of the resulting records.

        Each record is opened in its own tab rather than by navigating the
        results tab, so the results survive and the search CAPTCHA is paid
        once for the whole page instead of once per record.
        """
        from selenium.webdriver.common.by import By

        links = self.search(year=year, organisation=organisation, keyword=keyword)
        if not links:
            return []

        results_tab = self._driver.current_window_handle
        records: list[AwardRecord] = []

        for link in links[:limit]:
            crashed = False
            try:
                # Opened from the results page, so the browser sends it the
                # way a click would.
                self._driver.switch_to.window(results_tab)
                anchors = [
                    element
                    for element in self._driver.find_elements(By.TAG_NAME, "a")
                    if (element.get_attribute("href") or "") == link
                ]
                if not anchors:
                    logger.warning("award_link_vanished", url=link[:90])
                    continue

                self._driver.execute_script(
                    "arguments[0].setAttribute('target','_blank'); arguments[0].click();",
                    anchors[0],
                )
                time.sleep(self._settle)

                opened = [h for h in self._driver.window_handles if h != results_tab]
                if not opened:
                    logger.warning("award_tab_not_opened", url=link[:90])
                    continue
                self._driver.switch_to.window(opened[-1])
                time.sleep(1)

                record = self.read_detail_here(source_url=link)
                if record is not None:
                    records.append(record)
            except Exception as exc:
                logger.warning("award_detail_failed", url=link[:90], error=str(exc)[:160])
                if session_is_dead(exc):
                    # The results tab died with the browser, so every
                    # remaining link is unreachable: replace the browser and
                    # search again for whatever is still owed.
                    crashed = True
                    if not self.restart():
                        break
                    remaining = limit - len(records)
                    if remaining > 0:
                        records.extend(
                            self.harvest(
                                year=year,
                                organisation=organisation,
                                keyword=keyword,
                                limit=remaining,
                            )
                        )
                    break
            finally:
                # Close every tab but the results tab, whatever happened, so
                # one bad record cannot leave the browser somewhere unknown.
                #
                # Skipped after a crash: the handles belong to the browser
                # that died, and the replacement has never seen them. Guarded
                # regardless, because this runs in a finally and a throw here
                # would replace the real error with one about window handles.
                if not crashed:
                    with contextlib.suppress(Exception):
                        for handle in list(self._driver.window_handles):
                            if handle != results_tab:
                                self._driver.switch_to.window(handle)
                                self._driver.close()
                        self._driver.switch_to.window(results_tab)

        logger.info("award_harvest_done", found=len(links), read=len(records))
        return records
