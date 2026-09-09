"""Browser-driven download of tender document packs from the CPPP portal.

Why a real browser. The listing pages this project reads are plain,
unauthenticated HTML. The document packs are different twice over: the tender
detail pages are assembled by Drupal behaviour code that a bare HTTP GET does
not satisfy, and the download itself leads through a CAPTCHA form. Chrome
satisfies both, so this module drives one the way a bidder would: open the
tender's detail page, follow "Download as zip file", read the CAPTCHA image
with the OCR in ``captcha``, type the answer, and retry on a fresh image when
the portal rejects the read.

The CAPTCHA exists to make bulk retrieval awkward, so this behaves the way the
listing scraper does, only more so: one tender at a time, a bounded number of
attempts per tender, a pause between tenders, and nothing resembling parallel
access. The documents themselves are public — this automates the queueing,
not the authorisation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as conditions
from selenium.webdriver.support.ui import WebDriverWait

from app.core.logging import get_logger
from app.corpus.captcha import is_plausible, solve
from app.corpus.cppp import LISTINGS, ScrapedTender, _page_url

logger = get_logger(__name__)

DownloadStatus = Literal[
    "downloaded",
    "not_found",
    "no_download_link",
    "captcha_exhausted",
    "download_timeout",
    "failed",
]


@dataclass(frozen=True)
class DownloadResult:
    """One tender's outcome, whatever it was."""

    reference: str
    tender_id: str | None
    status: DownloadStatus
    attempts: int = 0
    zip_path: Path | None = None
    error: str | None = None


def wait_for_new_zip(
    directory: Path,
    before: set[str],
    *,
    timeout: float,
    stability_polls: int = 2,
) -> Path | None:
    """Wait for a finished ZIP that was not in ``before``.

    Chrome streams a download through a ``.crdownload`` partner file and only
    then renames it, so partial files are ignored, and a candidate must hold
    its size across consecutive polls before it is called finished — Chrome
    creates the name before the bytes arrive.
    """
    deadline = time.monotonic() + timeout
    candidate: Path | None = None
    previous_size = -1
    stable_polls = 0

    while time.monotonic() < deadline:
        zips = [path for path in directory.glob("*.zip") if path.name not in before]
        if zips:
            newest = max(zips, key=lambda path: path.stat().st_mtime)
            size = newest.stat().st_size
            if newest == candidate and size == previous_size and size > 0:
                stable_polls += 1
                if stable_polls >= stability_polls:
                    return newest
            else:
                candidate, previous_size, stable_polls = newest, size, 1
        time.sleep(0.5)

    return None


def tender_from_row(row: dict[str, Any]) -> ScrapedTender:
    """Rebuild a tender from a cache row, tolerating extra and missing keys.

    The cache is written by the scraper and annotated by the downloader, so a
    row can carry keys the dataclass does not know, and rows scraped before a
    field was added lack it. Neither may crash the run.
    """
    known = {
        name: row[name] for name in ScrapedTender.__dataclass_fields__ if name in row
    }
    return ScrapedTender(**known)  # type: ignore[arg-type]


def _first_present(
    driver: Any,
    selectors: tuple[tuple[str, str], ...],
    *,
    timeout: float,
) -> Any | None:
    """First element matching any selector, tried in order.

    Short waits per selector rather than one long one: the families are
    alternatives, not a queue, and a page carries at most one of them.
    """
    per_selector = max(timeout / len(selectors), 0.5)
    for by, selector in selectors:
        try:
            return WebDriverWait(driver, per_selector).until(
                conditions.presence_of_element_located((by, selector))
            )
        except TimeoutException:
            continue
    return None


# Two generations of the portal's CAPTCHA markup. The GeM-CPPP Drupal build
# (current) tags its form fields with data-drupal-selector attributes; the
# legacy NIC build that the tenderX reference project targeted uses bare ids.
# Both families are tried, current first, so the module survives whichever
# page a tender's flow lands on.
CAPTCHA_IMAGE_SELECTORS = (
    (By.CSS_SELECTOR, "img[data-drupal-selector='edit-captcha-image']"),
    (By.CSS_SELECTOR, "div.captcha img"),
    (By.ID, "captchaImage"),
)
CAPTCHA_INPUT_SELECTORS = (
    (By.ID, "edit-captcha-response"),
    (By.NAME, "captcha_response"),
    (By.ID, "captchaText"),
)
CAPTCHA_REFRESH_SELECTORS = (
    (By.CSS_SELECTOR, "a.reload-captcha"),
    (By.CSS_SELECTOR, "a[href*='image-captcha-refresh']"),
    (By.ID, "captcha"),
)
SUBMIT_SELECTORS = (
    (By.CSS_SELECTOR, "input[type='submit']"),
    (By.ID, "Submit"),
    (By.ID, "submit"),
)

# The portal words its rejections differently across builds; matching the
# common fragments is wider than matching any one sentence.
REJECTION_MARKERS = (
    "invalid captcha",
    "captcha was not correct",
    "not correct",
    "wrong captcha",
    "captcha did not match",
)


class TenderDocumentDownloader:
    """One browser, one tender at a time. Use as a context manager."""

    # Case-insensitive link matching via translate(), because the portal's own
    # capitalisation has drifted between builds.
    _LINK_XPATHS = (
        "//a[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
        " 'abcdefghijklmnopqrstuvwxyz'), 'download as zip')]",
        "//a[contains(translate(@href, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
        " 'abcdefghijklmnopqrstuvwxyz'), '.zip')]",
        "//a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
        " 'abcdefghijklmnopqrstuvwxyz'), 'download') and contains(translate(.,"
        " 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'zip')]",
    )

    def __init__(
        self,
        download_dir: Path | str,
        *,
        headless: bool = True,
        max_captcha_attempts: int = 8,
        page_load_timeout: float = 90.0,
        download_timeout: float = 120.0,
        listing_search_pages: int = 5,
        debug_dir: Path | str | None = None,
    ) -> None:
        self._download_dir = Path(download_dir)
        self._headless = headless
        self._max_attempts = max(1, max_captcha_attempts)
        self._page_load_timeout = page_load_timeout
        self._download_timeout = download_timeout
        self._listing_search_pages = listing_search_pages
        self._debug_dir = Path(debug_dir) if debug_dir else None
        self._driver: webdriver.Chrome | None = None

    def __enter__(self) -> TenderDocumentDownloader:
        self._driver = self._build_driver()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._driver is not None:
            self._driver.quit()
            self._driver = None

    def _build_driver(self) -> webdriver.Chrome:
        self._download_dir.mkdir(parents=True, exist_ok=True)
        options = Options()
        if self._headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1440,900")
        # The pack is a ZIP: these stop Chrome asking where to put it, and
        # stop it offering its PDF viewer instead of saving the file.
        options.add_experimental_option(
            "prefs",
            {
                "download.default_directory": str(self._download_dir),
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "plugins.always_open_pdf_externally": True,
                "safebrowsing.enabled": True,
            },
        )
        # Selenium Manager resolves a matching chromedriver, so no separate
        # webdriver-manager dependency or pre-installed driver is needed.
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(self._page_load_timeout)
        return driver

    def download(self, tender: ScrapedTender) -> DownloadResult:
        """Take one tender from its detail page to a saved ZIP."""
        driver = self._require_driver()
        before = {path.name for path in self._download_dir.glob("*.zip")}
        logger.info("download_start", reference=tender.reference)

        try:
            if not self._open_tender_page(tender):
                return DownloadResult(
                    tender.reference,
                    tender.tender_id,
                    "not_found",
                    error="No detail page and no listing row matched.",
                )

            link = self._find_download_link()
            if link is None:
                logger.warning(
                    "download_link_absent", reference=tender.reference, page_title=driver.title
                )
                return DownloadResult(tender.reference, tender.tender_id, "no_download_link")

            # The link leads to the CAPTCHA gate, sometimes in a new tab.
            self._click(link)
            self._adopt_new_tab()

            attempts = self._pass_captcha_gate()
            if attempts is None:
                return DownloadResult(
                    tender.reference,
                    tender.tender_id,
                    "captcha_exhausted",
                    attempts=self._max_attempts,
                )

            # With the gate passed the portal either streams the file straight
            # away or returns to a page where the link now delivers it.
            link = self._find_download_link()
            if link is not None:
                self._click(link)
                self._adopt_new_tab()

            zip_path = wait_for_new_zip(self._download_dir, before, timeout=self._download_timeout)
            if zip_path is None:
                return DownloadResult(
                    tender.reference, tender.tender_id, "download_timeout", attempts=attempts
                )

            logger.info(
                "download_complete",
                reference=tender.reference,
                zip=zip_path.name,
                attempts=attempts,
            )
            return DownloadResult(
                tender.reference,
                tender.tender_id,
                "downloaded",
                attempts=attempts,
                zip_path=zip_path,
            )
        except Exception as exc:  # a browser session can fail in many ways
            logger.error("download_failed", reference=tender.reference, error=str(exc))
            return DownloadResult(tender.reference, tender.tender_id, "failed", error=str(exc))
        finally:
            self._return_to_base_tab()

    # --- navigation --------------------------------------------------------- #

    def _open_tender_page(self, tender: ScrapedTender) -> bool:
        if tender.detail_url:
            self._require_driver().get(tender.detail_url)
            return True
        return self._open_via_listing(tender)

    def _open_via_listing(self, tender: ScrapedTender) -> bool:
        """Find the tender by walking the public listing, row by row.

        For rows scraped before detail URLs were recorded. It is the same
        listing the HTTP scraper reads; here the browser opens it, so the
        row's own link carries whatever session context the portal wants.
        """
        listing = LISTINGS.get(tender.source_listing, LISTINGS["high_value"])
        driver = self._require_driver()
        for page in range(1, self._listing_search_pages + 1):
            driver.get(_page_url(listing, page))
            rows = driver.find_elements(By.CSS_SELECTOR, "table#table tbody tr")
            for row in rows:
                text = row.text or ""
                if tender.reference in text or (tender.tender_id and tender.tender_id in text):
                    self._click(row.find_element(By.CSS_SELECTOR, "td:nth-child(5) a"))
                    self._adopt_new_tab()
                    return True
            logger.info("listing_page_scanned", page=page, reference=tender.reference)
        return False

    # --- the CAPTCHA gate ---------------------------------------------------- #

    def _pass_captcha_gate(self) -> int | None:
        """Answer the gate until the portal stops asking. None when stuck."""
        driver = self._require_driver()
        attempts = 0

        while attempts < self._max_attempts:
            if _first_present(driver, CAPTCHA_IMAGE_SELECTORS, timeout=4) is None:
                return attempts  # no gate in the way, or already answered

            attempts += 1
            if attempts > 1:
                # A fresh image beats re-reading one the portal just rejected.
                self._refresh_captcha()

            image = _first_present(driver, CAPTCHA_IMAGE_SELECTORS, timeout=4)
            if image is None:
                return attempts

            answer = self._read_captcha(image)
            if not is_plausible(answer):
                logger.info("captcha_unreadable", attempt=attempts, raw=answer)
                continue

            box = _first_present(driver, CAPTCHA_INPUT_SELECTORS, timeout=4)
            submit = _first_present(driver, SUBMIT_SELECTORS, timeout=4)
            if box is None or submit is None:
                return attempts  # the gate vanished mid-read; treat as passed

            box.clear()
            box.send_keys(answer)
            self._click(submit)
            time.sleep(1.5)  # the portal answers with a reload or an error banner

            if self._captcha_rejected():
                logger.info("captcha_rejected", attempt=attempts, answer=answer)
                continue
            return attempts

        return None

    def _read_captcha(self, image: Any) -> str:
        return solve(image.screenshot_as_png, debug_dir=self._debug_dir)

    def _refresh_captcha(self) -> None:
        refresh = _first_present(self._require_driver(), CAPTCHA_REFRESH_SELECTORS, timeout=2)
        if refresh is not None:
            self._click(refresh)
            time.sleep(0.8)

    def _captcha_rejected(self) -> bool:
        driver = self._require_driver()
        source = (driver.page_source or "").lower()
        if any(marker in source for marker in REJECTION_MARKERS):
            return True

        # No banner, but the gate re-rendered: also a rejection — unless the
        # portal moved on and re-showed the download link instead, which means
        # the answer was accepted after all.
        if _first_present(driver, CAPTCHA_IMAGE_SELECTORS, timeout=2) is None:
            return False
        return self._find_download_link() is None

    # --- browser mechanics ---------------------------------------------------- #

    def _find_download_link(self) -> Any | None:
        driver = self._require_driver()
        for xpath in self._LINK_XPATHS:
            for element in driver.find_elements(By.XPATH, xpath):
                if element.is_displayed():
                    return element
        return None

    def _click(self, element: Any) -> None:
        # A plain click is intercepted by the portal's sticky headers; a JS
        # click dispatches straight to the element.
        self._require_driver().execute_script("arguments[0].click();", element)

    def _adopt_new_tab(self) -> None:
        driver = self._require_driver()
        handles = driver.window_handles
        if len(handles) > 1 and driver.current_window_handle != handles[-1]:
            driver.switch_to.window(handles[-1])

    def _return_to_base_tab(self) -> None:
        driver = self._driver
        if driver is None:
            return
        for handle in driver.window_handles[1:]:
            driver.switch_to.window(handle)
            driver.close()
        if driver.window_handles:
            driver.switch_to.window(driver.window_handles[0])

    def _require_driver(self) -> webdriver.Chrome:
        if self._driver is None:
            raise RuntimeError("Use TenderDocumentDownloader as a context manager.")
        return self._driver