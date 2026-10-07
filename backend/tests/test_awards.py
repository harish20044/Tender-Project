"""Parsing an award-of-contract page.

The parser is tested against the portal's real markup, kept here as a
fixture, because the shape is what breaks: the page puts two label/value
pairs in one row, wraps some values in a div and others not, and separates
label from value with a colon cell. A parser written against a tidied-up
version of that passes while reading the wrong column in production.
"""

from __future__ import annotations

from app.corpus.awards import AwardRecord, awarded_date_of, parse_award_detail

# Trimmed from a live page, keeping every structural quirk: the colon cells,
# the two-pairs-per-row layout for bid count and contract value, and the
# div-wrapped values for organisation and address.
REAL_PAGE = """
<table width="100%">
<tbody>
<tr>
  <td class="black v-a-top" width="29%">Organisation Name</td>
  <td class="v-a-top black" width="1%"><b>:</b></td>
  <td colspan="4" id="tenderDetailDivTd" width="70%">
    <div class="event-dtl word-break line-height"> BHEL Hyderabad</div>
  </td>
</tr>
<tr>
  <td class="black" width="29%">Tender Ref. No.</td>
  <td width="1%"><b>:<b></b></b></td>
  <td colspan="4">B9AYM01525</td>
</tr>
<tr>
  <td class="black" width="29%">Tender Description</td>
  <td width="1%"><b>:<b></b></b></td>
  <td colspan="4">CONSTRUCTION OF ROAD OVER BRIDGE</td>
</tr>
<tr>
  <td class="black" width="29%">Tender Type</td>
  <td width="1%"><b>:<b></b></b></td>
  <td width="20%">1</td>
  <td class="black" width="29%">Number of bids received</td>
  <td width="1%"><b>:<b></b></b></td>
  <td width="20%">7</td>
</tr>
<tr>
  <td class="black" width="29%">Name of the selected bidder(s)</td>
  <td width="1%"><b>:<b></b></b></td>
  <td width="20%">DOXA ENGINEERING WORKS</td>
  <td class="black" width="29%">Contract Value <span class="mand">&nbsp;*</span></td>
  <td width="1%"><b>:<b></b></b></td>
  <td width="20%">2,84,50,000</td>
</tr>
<tr>
  <td colspan="6"><span class="mand">&nbsp;*</span>&nbsp;Currency regarding Contract
  Value may please be checked with the corresponding tender portals/websites.</td>
</tr>
<tr>
  <td class="black v-a-top" width="29%">Address of the selected bidder(s)</td>
  <td class="v-a-top black" width="1%"><b>:</b></td>
  <td colspan="4" id="tenderDetailDivTd" width="70%">
    <div class="event-dtl word-break line-height"> PLOT NO.- 24-91, HYDERABAD </div>
  </td>
</tr>
<tr>
  <td class="black" width="29%">Published Date</td>
  <td width="1%"><b>:<b></b></b></td>
  <td width="20%">20-Dec-2026 09:00 AM</td>
  <td class="black" width="29%">Contract Date</td>
  <td width="1%"><b>:<b></b></b></td>
  <td width="20%">22-Dec-2025 09:00 AM</td>
</tr>
<tr>
  <td class="black v-a-top" width="29%">Date of Completion/Completion Period in Days</td>
  <td class="v-a-top black" width="1%"><b>:</b></td>
  <td colspan="4" id="tenderDetailDivTd" width="70%">
    <div class="event-dtl word-break line-height"> 20-Feb-2026 09:00 AM </div>
  </td>
</tr>
</tbody>
</table>
"""


class TestParsing:
    def test_reads_every_published_field(self) -> None:
        record = parse_award_detail(REAL_PAGE, source_url="https://example.test/aoc")

        assert record.reference == "B9AYM01525"
        assert record.organisation == "BHEL Hyderabad"
        assert record.description == "CONSTRUCTION OF ROAD OVER BRIDGE"
        assert record.selected_bidder == "DOXA ENGINEERING WORKS"
        assert record.bidder_address == "PLOT NO.- 24-91, HYDERABAD"
        assert record.source_url == "https://example.test/aoc"

    def test_reads_the_pair_that_shares_a_row(self) -> None:
        # "Tender Type" and "Number of bids received" sit in one row, as do
        # the bidder name and the contract value. A row-based parser reads
        # the second label as the first value and silently gets both wrong.
        record = parse_award_detail(REAL_PAGE)
        assert record.bids_received == 7
        assert record.contract_value == 28_450_000

    def test_strips_indian_digit_grouping(self) -> None:
        record = parse_award_detail(REAL_PAGE)
        assert record.contract_value == 28_450_000

    def test_dates_become_iso(self) -> None:
        record = parse_award_detail(REAL_PAGE)
        assert record.published_date == "2026-12-20"
        assert record.contract_date == "2025-12-22"
        # The label contains a slash, which must not fuse "Completion" and
        # "Completion" into one word during normalisation.
        assert record.completion_date == "2026-02-20"

    def test_value_is_always_flagged_unverified(self) -> None:
        # The portal states no currency for the figure, so nothing may
        # present it as a checked rupee amount.
        assert parse_award_detail(REAL_PAGE).value_is_unverified is True

    def test_zero_value_is_not_stated_rather_than_nil(self) -> None:
        # The portal writes 0 where no value was published. Reading that as
        # a nil contract would poison every comparison it took part in.
        page = REAL_PAGE.replace("2,84,50,000", "0")
        assert parse_award_detail(page).contract_value is None

    def test_zero_bids_is_a_real_outcome(self) -> None:
        # Unlike a value, no bids received is meaningful and is kept.
        page = REAL_PAGE.replace(">7</td>", ">0</td>")
        assert parse_award_detail(page).bids_received == 0

    def test_empty_page_yields_an_empty_record(self) -> None:
        record = parse_award_detail("<html><body>Invalid Url.Please Check</body></html>")
        assert record.reference is None
        assert record.is_useful is False


class TestUsefulness:
    def test_record_with_an_outcome_is_useful(self) -> None:
        assert parse_award_detail(REAL_PAGE).is_useful is True

    def test_reference_alone_is_not_useful(self) -> None:
        # A rate-contract notice with no bidder, no count and no value adds
        # nothing to a comparison, so it is not stored.
        record = AwardRecord(reference="X/1", description="something")
        assert record.is_useful is False

    def test_bid_count_alone_is_enough(self) -> None:
        assert AwardRecord(reference="X/1", bids_received=0).is_useful is True

    def test_no_reference_is_never_useful(self) -> None:
        assert AwardRecord(selected_bidder="Someone", bids_received=4).is_useful is False


class TestAwardedDate:
    def test_prefers_the_contract_date(self) -> None:
        record = parse_award_detail(REAL_PAGE)
        awarded = awarded_date_of(record)
        assert awarded is not None
        assert awarded.isoformat() == "2025-12-22"

    def test_falls_back_to_published_date(self) -> None:
        record = AwardRecord(reference="X/1", published_date="2026-03-04")
        awarded = awarded_date_of(record)
        assert awarded is not None
        assert awarded.isoformat() == "2026-03-04"

    def test_none_when_neither_is_stated(self) -> None:
        assert awarded_date_of(AwardRecord(reference="X/1")) is None


class TestDeadSessionSharing:
    """The award scraper and the downloader must agree on what a crash is.

    They drive the same portals through long page-load sequences, and Chrome
    dies every few loads on some machines. A scraper that misses a crash
    spends its whole CAPTCHA budget against a dead browser and reports every
    remaining record as a content failure — the most misleading way for a
    batch to end.
    """

    def test_the_real_chrome_crash_message_is_recognised(self) -> None:
        from app.corpus.browser import session_is_dead

        message = (
            "invalid session id: session deleted as the browser has closed the connection\n"
            "from disconnected: not connected to DevTools\n"
            "(Session info: chrome=155.0.8059.40)"
        )
        assert session_is_dead(Exception(message))

    def test_a_closed_window_counts_as_dead(self) -> None:
        from app.corpus.browser import session_is_dead

        assert session_is_dead(Exception("no such window: target window already closed"))

    def test_an_ordinary_failure_does_not(self) -> None:
        from app.corpus.browser import session_is_dead

        assert not session_is_dead(Exception("element click intercepted"))
        assert not session_is_dead(TimeoutError("timed out waiting for element"))

    def test_both_scrapers_use_the_same_check(self) -> None:
        # The downloader delegates rather than keeping its own copy, so the
        # two cannot drift apart as new crash phrasings turn up.
        from app.corpus.browser import session_is_dead
        from app.corpus.documents import TenderDocumentDownloader

        message = "chrome not reachable"
        assert TenderDocumentDownloader._session_is_dead(Exception(message)) is True
        assert session_is_dead(Exception(message)) is True


class TestRestartBudget:
    def test_the_budget_is_bounded(self) -> None:
        # A browser that dies instantly and forever is a broken environment.
        # Retrying without limit would hide that behind a run that never ends.
        from app.corpus.awards import AwardScraper

        scraper = AwardScraper(max_session_restarts=0)
        assert scraper.restart() is False

    def test_a_negative_budget_is_clamped(self) -> None:
        from app.corpus.awards import AwardScraper

        assert AwardScraper(max_session_restarts=-5)._max_session_restarts == 0


class TestResultTotalParsing:
    """Telling 'the search found nothing' from 'the CAPTCHA was refused'.

    The search form stays on the results page, CAPTCHA and all, so asking
    "is the CAPTCHA still showing?" is true even on success. It cannot tell
    a rejected answer from a search that legitimately matched nothing, and a
    keyword matching nothing therefore burned the entire attempt budget —
    about six minutes per keyword — before reporting the wrong reason.

    The results count is the honest signal: absent means the search never
    ran, zero means it ran and matched nothing.
    """

    def test_reads_the_count(self) -> None:
        from app.corpus.awards import _RESULT_TOTAL

        match = _RESULT_TOTAL.search("Total AOCs : 55200 « Previous 1 2 3")
        assert match is not None
        assert int(match.group(1).replace(",", "")) == 55200

    def test_reads_a_grouped_count(self) -> None:
        from app.corpus.awards import _RESULT_TOTAL

        match = _RESULT_TOTAL.search("Total AOCs: 1,234")
        assert match is not None
        assert int(match.group(1).replace(",", "")) == 1234

    def test_zero_is_a_real_answer_not_an_absence(self) -> None:
        from app.corpus.awards import _RESULT_TOTAL

        match = _RESULT_TOTAL.search("Total AOC : 0")
        assert match is not None
        assert int(match.group(1)) == 0

    def test_singular_and_spacing_variants(self) -> None:
        from app.corpus.awards import _RESULT_TOTAL

        for text in ("Total AOC : 7", "total aocs:7", "Total  AOCs   :  7"):
            match = _RESULT_TOTAL.search(text)
            assert match is not None, text
            assert int(match.group(1)) == 7

    def test_absent_on_a_page_that_never_ran_the_search(self) -> None:
        from app.corpus.awards import _RESULT_TOTAL

        page = "What code is in the image? Enter the characters shown in the image."
        assert _RESULT_TOTAL.search(page) is None
