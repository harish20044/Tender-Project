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

import base64
import binascii
import contextlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from selenium import webdriver
from selenium.common.exceptions import (
    InvalidSessionIdException,
    NoAlertPresentException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as conditions
from selenium.webdriver.support.ui import WebDriverWait

from app.core.logging import get_logger
from app.corpus.captcha import is_plausible, solve
from app.corpus.cppp import LISTINGS, ScrapedTender, _page_url

logger = get_logger(__name__)

# CPPP wraps every outbound tender-document link in its own redirector, with
# the destination base64-encoded in the path.
REDIRECT_PATH = "/cppp/tenderredirect/by/"

# Phrases the department portals use when they want a Digital Signature
# Certificate before they will show anything. DSCHandler is the local service
# that talks to the bidder's hardware token.
SIGNATURE_REQUIRED_MARKERS = (
    "dschandler",
    "digital signature",
    "dsc is not",
    "please start it to login",
    "signer is not running",
)

DownloadStatus = Literal[
    "downloaded",
    "not_found",
    "no_download_link",
    "captcha_exhausted",
    "download_timeout",
    "requires_signature",
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


def decode_redirect_target(href: str) -> str | None:
    """The real destination behind a CPPP ``tenderredirect`` link.

    CPPP no longer links straight out to the issuing department. It links to
    ``/cppp/tenderredirect/by/<base64 of the destination>``, so the hop stays
    on its own domain and the target is recoverable without following it —
    which is how the department portal can be identified and logged before a
    click, and how a redirect that merely points back into CPPP is told apart
    from one that leads to a document pack.

    Returns None when ``href`` is not a redirect, or when the payload will
    not decode, so a caller can fall back to judging the href itself.
    """
    marker = REDIRECT_PATH
    position = href.lower().find(marker)
    if position == -1:
        return None

    payload = href[position + len(marker) :].split("/")[0].split("?")[0].strip()
    if not payload:
        return None

    # The portal omits base64 padding in some builds; "==" is always safe to
    # add because the decoder ignores surplus padding.
    try:
        decoded = base64.b64decode(payload + "==", validate=False)
    except (ValueError, binascii.Error):
        return None

    try:
        target = decoded.decode("utf-8")
    except UnicodeDecodeError:
        return None

    return target if target.lower().startswith("http") else None


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
        max_session_restarts: int = 2,
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
        # Chrome dies mid-run on some machines, an auto-update under a live
        # session being the usual cause. A restart budget turns that into a
        # retry rather than the end of the batch.
        self._max_session_restarts = max(0, max_session_restarts)
        self._debug_dir = Path(debug_dir) if debug_dir else None
        # Set when a department portal demands a Digital Signature
        # Certificate, so the outcome can name that rather than report a
        # generic failure.
        self._signature_wall: str | None = None
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

    @staticmethod
    def _session_is_dead(exc: BaseException) -> bool:
        """Whether an exception means the browser itself has gone.

        Chrome does die mid-run — an auto-update swapping the binary under a
        live session is the usual cause, and a long CAPTCHA sequence is
        exactly the kind of workload that outlives one. Once it has, every
        subsequent call fails identically, so a batch that does not notice
        reports every remaining tender as a download failure.
        """
        text = str(exc).lower()
        return isinstance(exc, InvalidSessionIdException) or any(
            marker in text
            for marker in (
                "invalid session id",
                "browser has closed the connection",
                "not connected to devtools",
                "chrome not reachable",
                "disconnected",
            )
        )

    def _restart_driver(self) -> None:
        """Replace a dead browser with a fresh one."""
        logger.warning("browser_restarting")
        if self._driver is not None:
            # Already gone is the normal case here, which is what brought us.
            with contextlib.suppress(WebDriverException):
                self._driver.quit()
            self._driver = None
        self._driver = self._build_driver()

    def download(self, tender: ScrapedTender) -> DownloadResult:
        """Take one tender from its detail page to a saved ZIP.

        Retries once on a fresh browser if the session dies mid-attempt, so a
        Chrome crash costs one tender's work rather than the whole batch.
        """
        for attempt in range(1, self._max_session_restarts + 2):
            try:
                return self._download_once(tender)
            except WebDriverException as exc:
                if not self._session_is_dead(exc) or attempt > self._max_session_restarts:
                    return DownloadResult(
                        tender.reference, tender.tender_id, "failed", error=str(exc)[:300]
                    )
                logger.warning(
                    "download_session_lost",
                    reference=tender.reference,
                    attempt=attempt,
                    error=str(exc)[:120],
                )
                self._restart_driver()

        return DownloadResult(
            tender.reference,
            tender.tender_id,
            "failed",
            error="The browser could not be kept alive long enough to finish.",
        )

    def _download_once(self, tender: ScrapedTender) -> DownloadResult:
        """One attempt, on the browser as it currently stands."""
        driver = self._require_driver()
        before = {path.name for path in self._download_dir.glob("*.zip")}
        self._signature_wall = None
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
                if self._signature_wall:
                    logger.info(
                        "portal_requires_signature",
                        reference=tender.reference,
                        message=self._signature_wall[:200],
                    )
                    return DownloadResult(
                        tender.reference,
                        tender.tender_id,
                        "requires_signature",
                        attempts=attempts,
                        error=self._signature_wall[:300],
                    )
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
            # A dead browser is the caller's to handle: it restarts the
            # session and tries again. Swallowing it here would turn every
            # tender after the crash into a download failure and hide the
            # one problem that is actually recoverable.
            if self._session_is_dead(exc):
                raise
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
    # CPPP marks the link to the issuing department's portal with its own
    # class, which is a far better signal than guessing at URL shapes: it is
    # the portal naming the link itself. There is exactly one per detail page.
    _DEPARTMENT_LINK_SELECTOR = "a.tndr_redirect"
    _DEPARTMENT_LINK_XPATH = (
        "//*[normalize-space(text())='Tender Document']/following::a[starts-with(@href, 'http')][1]"
    )
    # Fallback, for a page that labels it differently: NIC's GePNIC
    # deployments (the large majority) and the handful of bespoke portals.
    _DEPARTMENT_LINK_MARKERS = (
        "tnid",
        "tenderdetails",
        "tenderview",
        "tenderdocument",
        REDIRECT_PATH,
    )
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
        """Whether an href leads out to the portal that holds the documents.

        CPPP routes these through its own redirector now, so a link to the
        department sits on ``eprocure.gov.in/cppp`` like everything else and
        names its destination in a base64 path segment. Rejecting every CPPP
        URL — which is what this used to do — therefore threw away the only
        link that mattered, and the download stopped one hop short of the
        files with "no download link" as the explanation.

        For a redirect the destination is decoded and judged; for a direct
        link the href itself is.

        Takes the href exactly as the page gives it, never lowercased: the
        destination is base64, which is case-sensitive, so folding the case
        of the href corrupts the payload and the link gets rejected for
        being undecodable. Case folding happens below, on the decoded
        target, where it is safe.
        """
        lowered = href.lower()
        if not lowered.startswith("http"):
            return False

        target = decode_redirect_target(href)
        if target is not None:
            candidate = target.lower()
            # A redirect that points back into CPPP is navigation, not a pack.
            if "eprocure.gov.in/cppp" in candidate:
                return False
        elif "eprocure.gov.in/cppp" in lowered:
            return False
        else:
            candidate = lowered

        return not any(host in candidate for host in self._NON_TENDER_HOSTS)

    def _department_link(self) -> Any | None:
        driver = self._require_driver()

        # The portal's own class for this link, which beats every heuristic
        # below it when present.
        for element in driver.find_elements(By.CSS_SELECTOR, self._DEPARTMENT_LINK_SELECTOR):
            if self._is_department_link(element.get_attribute("href") or ""):
                return element

        for element in driver.find_elements(By.XPATH, self._DEPARTMENT_LINK_XPATH):
            if self._is_department_link(element.get_attribute("href") or ""):
                return element

        for element in driver.find_elements(By.TAG_NAME, "a"):
            href = element.get_attribute("href") or ""
            if self._is_department_link(href) and any(
                marker in href.lower() for marker in self._DEPARTMENT_LINK_MARKERS
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
        href = element.get_attribute("href") or ""
        target = decode_redirect_target(href)

        if target is not None:
            # Navigated to directly rather than clicked. CPPP's redirector
            # anchors carry rel="noreferrer", so following one sends no
            # Referer, and the redirector answers a refererless request with
            # a 302 back to the CPPP home page — the click appears to work
            # and silently lands nowhere. The destination is encoded in the
            # link itself, so the redirector adds nothing but a way to fail.
            logger.info("navigating_to_department_portal", url=target[:200], via=href[:120])
            driver.get(target)
        else:
            logger.info("following_department_link", url=href[:200])
            self._click(element)

        time.sleep(self._portal_settle_seconds)
        alert = self._dismiss_alert()
        if alert and self._needs_signature(alert):
            self._signature_wall = alert.strip()
            return False
        self._adopt_new_tab()

        current = driver.current_url.lower()
        if "unauthorizationpage" in current:
            return False
        # A redirector that bounced leaves us back on CPPP, which is not the
        # department portal however much it looks like a successful hop.
        if target is not None and "eprocure.gov.in/cppp" in current:
            logger.warning("department_portal_bounced", url=driver.current_url[:200])
            return False
        return True

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

    def _dismiss_alert(self) -> str | None:
        """Clear a native dialog, returning what it said.

        The department portals greet an unauthenticated visitor with a
        JavaScript alert, and while one is open every other WebDriver call
        fails with "unexpected alert open" — so the real message is lost and
        the run looks like a driver fault. Its text is the most informative
        thing on the page, so it is returned rather than discarded.
        """
        driver = self._driver
        if driver is None:
            return None
        try:
            alert = driver.switch_to.alert
            text = alert.text or ""
            alert.accept()
            logger.info("portal_alert_dismissed", text=text[:200])
            return text
        except NoAlertPresentException:
            return None
        except WebDriverException as exc:
            logger.warning("portal_alert_unreadable", error=str(exc)[:120])
            return None

    @staticmethod
    def _needs_signature(text: str) -> bool:
        """Whether a portal is asking for a Digital Signature Certificate.

        This is the boundary the project stops at. A DSC is a hardware token
        issued to a registered bidder, and the pack sits behind it: reaching
        the portal is not the same as being allowed in. Detected so the
        outcome can say so plainly, because "failed" invites someone to go
        looking for a bug that is not there.
        """
        lowered = text.lower()
        return any(marker in lowered for marker in SIGNATURE_REQUIRED_MARKERS)

    def _adopt_new_tab(self) -> None:
        driver = self._require_driver()
        handles = driver.window_handles
        if len(handles) > 1 and driver.current_window_handle != handles[-1]:
            driver.switch_to.window(handles[-1])

    def _return_to_base_tab(self) -> None:
        """Close any tab the download opened and go back to the first.

        Runs in a ``finally``, so it must not raise. A crashed browser makes
        every call here fail with an invalid session id, and an exception
        thrown from cleanup replaces the real error with a stack trace about
        window handles — which is how a Chrome crash came to look like a
        download bug. Tidying up is best-effort by nature: the session is
        discarded at the end of the batch regardless.
        """
        driver = self._driver
        if driver is None:
            return
        try:
            for handle in driver.window_handles[1:]:
                driver.switch_to.window(handle)
                driver.close()
            if driver.window_handles:
                driver.switch_to.window(driver.window_handles[0])
        except WebDriverException as exc:
            logger.warning("tab_cleanup_failed", error=f"{type(exc).__name__}: {str(exc)[:120]}")

    def _require_driver(self) -> webdriver.Chrome:
        if self._driver is None:
            raise RuntimeError("Use TenderDocumentDownloader as a context manager.")
        return self._driver
