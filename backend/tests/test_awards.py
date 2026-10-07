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
