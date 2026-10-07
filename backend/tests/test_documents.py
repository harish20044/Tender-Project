"""The document downloader's link resolution and crash recovery.

Both pieces tested here are ones that failed silently in production. The
redirect decoding is why the downloader reported "no download link" on every
tender for weeks: CPPP moved its outbound links behind its own redirector, so
the link to the department portal now lives on ``eprocure.gov.in/cppp`` —
exactly the host the matcher was written to exclude. The crash recovery is
why a single Chrome death turned every remaining tender in a batch into a
failure.

Neither needs a browser. The browser-driven parts are exercised by running
the script against the live portal, which is not something a unit test can
or should do.
"""

from __future__ import annotations

from selenium.common.exceptions import (
    InvalidSessionIdException,
    TimeoutException,
    WebDriverException,
)

from app.corpus.documents import REDIRECT_PATH, TenderDocumentDownloader, decode_redirect_target

# A real link, taken from a live Tender Full View page. The payload decodes
# to https://bpcltenders.eproc.in — the portal that holds the actual pack.
REAL_REDIRECT = (
    "https://eprocure.gov.in/cppp/tenderredirect/by/aHR0cHM6Ly9icGNsdGVuZGVycy5lcHJvYy5pbg=="
)


class TestRedirectDecoding:
    def test_decodes_a_real_redirect(self) -> None:
        assert decode_redirect_target(REAL_REDIRECT) == "https://bpcltenders.eproc.in"

    def test_decodes_a_longer_target_with_a_path(self) -> None:
        link = (
            "https://eprocure.gov.in/cppp/tenderredirect/by/"
            "aHR0cHM6Ly93d3cucHJvY3VyZW1lbnRpbmV0Lm9yZy9nb3Zlcm5tZW50LWUtcHJvY3VyZW1lbnQtc3lzdGVtLw=="
        )
        target = decode_redirect_target(link)
        assert target is not None
        assert target.startswith("https://www.procurementinet.org/")

    def test_a_direct_link_is_not_a_redirect(self) -> None:
        assert decode_redirect_target("https://bpcltenders.eproc.in/nicgep/app") is None

    def test_undecodable_payload_returns_none(self) -> None:
        # Garbage must not raise: the page is third-party HTML and the
        # caller falls back to judging the href itself.
        assert (
            decode_redirect_target(f"https://eprocure.gov.in{REDIRECT_PATH}!!!not-base64!!!")
            is None
        )

    def test_empty_payload_returns_none(self) -> None:
        assert decode_redirect_target(f"https://eprocure.gov.in{REDIRECT_PATH}") is None

    def test_payload_that_decodes_to_non_url_returns_none(self) -> None:
        # "aGVsbG8=" is "hello", which is not a destination.
        assert decode_redirect_target(f"https://eprocure.gov.in{REDIRECT_PATH}aGVsbG8=") is None


class TestDepartmentLinkPredicate:
    def _downloader(self, tmp_path: object) -> TenderDocumentDownloader:
        return TenderDocumentDownloader(str(tmp_path))

    def test_accepts_a_redirect_to_a_department_portal(self, tmp_path: object) -> None:
        # The regression that mattered: this href is on eprocure.gov.in/cppp,
        # which the predicate used to reject outright, and it is the only
        # link on the page that leads to the documents.
        assert self._downloader(tmp_path)._is_department_link(REAL_REDIRECT) is True

    def test_a_lowercased_redirect_is_rejected_as_undecodable(self, tmp_path: object) -> None:
        # Guards the bug this very test suite caught: the link finder used to
        # lowercase every href before the predicate saw it, which corrupts a
        # base64 payload and made the one link that mattered undecodable.
        # If the finder ever folds case again, this stays passing while the
        # test above fails, which points straight at the cause.
        assert self._downloader(tmp_path)._is_department_link(REAL_REDIRECT.lower()) is False

    def test_rejects_a_redirect_that_points_back_into_cppp(self, tmp_path: object) -> None:
        # base64 of "https://eprocure.gov.in/cppp/home" — navigation, not a pack.
        link = (
            "https://eprocure.gov.in/cppp/tenderredirect/by/"
            "aHR0cHM6Ly9lcHJvY3VyZS5nb3YuaW4vY3BwcC9ob21l"
        )
        assert self._downloader(tmp_path)._is_department_link(link) is False

    def test_rejects_a_redirect_to_page_furniture(self, tmp_path: object) -> None:
        # base64 of "https://play.google.com/store/apps" — the mobile app
        # link, which sits in the same document as a redirect too.
        link = (
            "https://eprocure.gov.in/cppp/tenderredirect/by/"
            "aHR0cHM6Ly9wbGF5Lmdvb2dsZS5jb20vc3RvcmUvYXBwcw=="
        )
        assert self._downloader(tmp_path)._is_department_link(link) is False

    def test_still_accepts_a_direct_department_link(self, tmp_path: object) -> None:
        # Older builds linked straight out, and some portals still do.
        link = "https://bpcltenders.eproc.in/nicgep/app?component=view&tnid=99"
        assert self._downloader(tmp_path)._is_department_link(link) is True

    def test_rejects_a_plain_cppp_page(self, tmp_path: object) -> None:
        assert (
            self._downloader(tmp_path)._is_department_link(
                "https://eprocure.gov.in/cppp/latestactivetendersnew"
            )
            is False
        )

    def test_rejects_a_non_http_href(self, tmp_path: object) -> None:
        assert self._downloader(tmp_path)._is_department_link("javascript:void(0)") is False


class TestDeadSessionDetection:
    """Telling a crashed browser apart from an ordinary failure.

    Only the first is worth restarting for. Treating a timeout as a crash
    would throw away a working session and re-run the CAPTCHA gate from
    scratch; treating a crash as ordinary turns every remaining tender in
    the batch into a failure.
    """

    def test_invalid_session_id_is_a_dead_session(self) -> None:
        assert TenderDocumentDownloader._session_is_dead(
            InvalidSessionIdException("invalid session id")
        )

    def test_the_real_chrome_crash_message_is_recognised(self) -> None:
        # Verbatim from a live run, after Chrome auto-updated mid-session.
        message = (
            "invalid session id: session deleted as the browser has closed the connection\n"
            "from disconnected: not connected to DevTools\n"
            "(Session info: chrome=155.0.8059.40)"
        )
        assert TenderDocumentDownloader._session_is_dead(WebDriverException(message))

    def test_chrome_not_reachable_is_a_dead_session(self) -> None:
        assert TenderDocumentDownloader._session_is_dead(WebDriverException("chrome not reachable"))

    def test_a_timeout_is_not_a_dead_session(self) -> None:
        assert not TenderDocumentDownloader._session_is_dead(TimeoutException(""))

    def test_an_ordinary_error_is_not_a_dead_session(self) -> None:
        assert not TenderDocumentDownloader._session_is_dead(
            WebDriverException("element click intercepted")
        )


class TestSignatureWall:
    """Recognising the authorisation boundary for what it is.

    The department portals release document packs only to a bidder holding a
    Digital Signature Certificate — a hardware token issued to a registered
    entity. Reaching the portal is not the same as being allowed in, and the
    project stops there deliberately. Detecting it matters because "failed"
    sends whoever reads the log hunting a bug that does not exist.
    """

    def test_the_real_bpcl_alert_is_recognised(self) -> None:
        # Verbatim from https://bpcltenders.eproc.in on a live run.
        message = (
            "It seems DSCHandler is not installed or is not started. "
            "If DSCHandler is installed, please start it to login!"
        )
        assert TenderDocumentDownloader._needs_signature(message)

    def test_a_digital_signature_prompt_is_recognised(self) -> None:
        assert TenderDocumentDownloader._needs_signature(
            "Please attach your Digital Signature Certificate to continue."
        )

    def test_matching_ignores_case(self) -> None:
        assert TenderDocumentDownloader._needs_signature("DSCHANDLER NOT RUNNING")

    def test_an_unrelated_alert_is_not_a_signature_wall(self) -> None:
        assert not TenderDocumentDownloader._needs_signature(
            "Your session has timed out. Please search again."
        )

    def test_empty_text_is_not_a_signature_wall(self) -> None:
        assert not TenderDocumentDownloader._needs_signature("")

    def test_requires_signature_is_a_valid_outcome(self) -> None:
        from app.corpus.documents import DownloadResult

        result = DownloadResult("REF/1", None, "requires_signature", error="DSCHandler")
        assert result.status == "requires_signature"
