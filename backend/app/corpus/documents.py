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
    known = {name: row[name] for name in ScrapedTender.__dataclass_fields__ if name in row}
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
        # The department portal is a separate site and a slower one; the hop
        # needs longer to settle than a click within CPPP does.
        self._portal_settle_seconds = 6.0
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

        # The prefs above only govern the tab the browser starts with, and the
        # pack is fetched from a link that opens a new one on another origin —
        # which headless Chrome silently declines to save. Browser-scoped
        # download behaviour covers every target, including tabs opened later.
        try:
            driver.execute_cdp_cmd(
                "Browser.setDownloadBehavior",
                {
                    "behavior": "allow",
                    "downloadPath": str(self._download_dir),
                    "eventsEnabled": True,
                },
            )
        except Exception as exc:  # older chromedriver: prefs remain the fallback
            logger.warning("download_behavior_unset", error=str(exc))

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

            # The gate comes first. The detail page carries the CAPTCHA form
            # itself, and the documents are not linked anywhere on it until
            # that form is answered — so looking for a download link before
            # passing the gate finds nothing and gives up a step too early.
            attempts = self._pass_captcha_gate()
            if attempts is None:
                return DownloadResult(
                    tender.reference,
                    tender.tender_id,
                    "captcha_exhausted",
                    attempts=self._max_attempts,
                )

            # CPPP publishes notices; it does not host the packs. Past the
            # gate the detail page carries a "Tender Document" link into the
            # issuing department's own portal, and that is where the files
            # actually live.
            if not self._follow_to_department_portal():
                logger.warning(
                    "department_link_absent",
                    reference=tender.reference,
                    page_title=driver.title,
                    attempts=attempts,
                )
                return DownloadResult(
                    tender.reference, tender.tender_id, "no_download_link", attempts=attempts
                )

            link = self._find_download_link()
            if link is None:
                logger.warning(
                    "download_link_absent",
                    reference=tender.reference,
                    page_title=driver.title,
                    url=driver.current_url[:200],
                    attempts=attempts,
                )
                return DownloadResult(
                    tender.reference, tender.tender_id, "no_download_link", attempts=attempts
                )

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

    # The portal serves this instead of the tender when a detail URL is used
    # outside the session that produced it.
    _INVALID_URL_MARKER = "invalid url"

    def _open_tender_page(self, tender: ScrapedTender) -> bool:
        """Open the tender's detail page, by whichever route actually works.

        A stored detail URL is tried first because it is one request rather
        than a walk through the listing, but the portal binds those URLs to
        the session that produced them: pasted into a fresh browser, even
        minutes later, they render "Invalid Url.Please Check" instead of the
        tender. So the result is checked, and the listing walk — which clicks
        the row's own link and therefore carries the session the portal
        wants — is the fallback rather than an afterthought.
        """
        driver = self._require_driver()

        if tender.detail_url:
            driver.get(tender.detail_url)
            if self._INVALID_URL_MARKER not in (driver.page_source or "").lower():
                return True
            logger.info("detail_url_rejected", reference=tender.reference)

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

    # The detail page labels the outbound link "Tender Document". Matched on
    # the label's own text node rather than `contains(., ...)`, which also
    # matches every ancestor holding that text — including <body>, whose
    # "following" links are the page footer, not the tender.
    _DEPARTMENT_LINK_XPATH = (
        "//*[normalize-space(text())='Tender Document']/following::a[starts-with(@href, 'http')][1]"
    )
    # Fallback, for a page that labels it differently: NIC's GePNIC
    # deployments (the large majority) and the handful of bespoke portals.
    _DEPARTMENT_LINK_MARKERS = ("tnid", "tenderdetails", "tenderview", "tenderdocument")
    # Page furniture that sits in the same document and would otherwise
    # satisfy "an external link": the mobile apps, and CPPP's own sibling
    # portals listed in the navigation.
    _NON_TENDER_HOSTS = (
        "apps.apple.com",
        "play.google.com",
        "facebook.com",
        "twitter.com",
        "x.com",
        "youtube.com",
        "linkedin.com",
        "instagram.com",
        "nic.in/nicgep/app",
    )

    def _is_department_link(self, href: str) -> bool:
        if not href.startswith("http") or "eprocure.gov.in/cppp" in href:
            return False
        return not any(host in href for host in self._NON_TENDER_HOSTS)

    def _department_link(self) -> Any | None:
        driver = self._require_driver()

        for element in driver.find_elements(By.XPATH, self._DEPARTMENT_LINK_XPATH):
            if self._is_department_link((element.get_attribute("href") or "").lower()):
                return element

        for element in driver.find_elements(By.TAG_NAME, "a"):
            href = (element.get_attribute("href") or "").lower()
            if self._is_department_link(href) and any(
                marker in href for marker in self._DEPARTMENT_LINK_MARKERS
            ):
                return element
        return None

    def _follow_to_department_portal(self) -> bool:
        """Click through to whichever portal actually holds the documents.

        Clicked rather than navigated to: these URLs are bound to the session
        that produced them, and fetching one directly returns GePNIC's
        "Unauthorized Page" exactly as a stored CPPP detail URL returns
        "Invalid Url". Returns False when the tender links nowhere, which is
        a tender whose pack CPPP simply does not point at.
        """
        element = self._department_link()
        if element is None:
            return False

        driver = self._require_driver()
        logger.info("following_department_link", url=(element.get_attribute("href") or "")[:200])
        self._click(element)
        time.sleep(self._portal_settle_seconds)
        self._adopt_new_tab()
        return "unauthorizationpage" not in driver.current_url.lower()

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
        """Get a different CAPTCHA image to read.

        Reloading the page is the fallback rather than the exception: this
        build of the portal renders the CAPTCHA inline on the detail page
        with no reload control beside it, and without a new image an
        unreadable one would simply be read again, identically, until the
        attempt budget ran out.
        """
        driver = self._require_driver()
        refresh = _first_present(driver, CAPTCHA_REFRESH_SELECTORS, timeout=2)
        if refresh is not None:
            self._click(refresh)
            time.sleep(0.8)
            return

        driver.refresh()
        time.sleep(1.5)

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
