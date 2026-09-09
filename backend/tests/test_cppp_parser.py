"""Tests for the procurement portal parser.

Run against a fixed HTML fragment rather than the live portal. CI must not
depend on a government website being reachable, and the classifier's behaviour
needs to be pinned against cases that actually caused wrong answers.
"""

from app.corpus.cppp import _classify, _parse_listing, _split_title

# Shaped like the portal's real markup: seven cells, serial number first, the
# title/reference/id joined with slashes.
LISTING_HTML = """
<table>
<tr><th>Sl.No</th><th>e-Published Date</th><th>Bid Submission Closing Date</th>
    <th>Tender Opening Date</th><th>Title/Ref.No./Tender Id</th>
    <th>Organisation Name</th><th>Corrigendum</th></tr>
<tr><td>1.</td><td>07-Sep-2026 08:41 PM</td><td>21-Sep-2026 02:00 PM</td>
    <td>21-Sep-2026 02:01 PM</td>
    <td><a href="https://eprocure.gov.in/cppp/tendersfullview/MTQxMTcyOTk=">Construction of
        Major Bridge at Km 27/NH-48/2026_NHAI_1234_1/998877</a></td>
    <td>National Highways Authority of India</td><td>2</td></tr>
<tr><td>2.</td><td>06-Sep-2026 10:00 AM</td><td>20-Sep-2026 03:00 PM</td>
    <td>20-Sep-2026 03:30 PM</td>
    <td>Regular cleaning of 8 nos weigh bridges/JNT/CED/26-27/17</td>
    <td>Northern Coalfields Limited</td><td>--</td></tr>
</table>
"""


def test_parses_data_rows_and_ignores_the_header() -> None:
    rows = _parse_listing(LISTING_HTML, "high_value")

    assert len(rows) == 2
    assert rows[0].organisation == "National Highways Authority of India"


def test_dates_are_parsed_into_iso() -> None:
    first = _parse_listing(LISTING_HTML, "high_value")[0]

    assert first.published_at == "2026-09-07T20:41:00"
    assert first.closing_at == "2026-09-21T14:00:00"


def test_corrigendum_count_reads_a_number_and_a_dash() -> None:
    rows = _parse_listing(LISTING_HTML, "high_value")

    assert rows[0].corrigendum_count == 2
    assert rows[1].corrigendum_count == 0


def test_trailing_numeric_segment_is_taken_as_the_tender_id() -> None:
    title, reference, tender_id = _split_title(
        "Construction of Major Bridge at Km 27/NH-48/2026_NHAI_1234_1/998877"
    )

    assert tender_id == "998877"
    assert reference == "2026_NHAI_1234_1"
    assert title.startswith("Construction of Major Bridge")


def test_a_title_without_a_numeric_id_still_yields_a_reference() -> None:
    _, reference, tender_id = _split_title("Widening of approach road/PWD/2026/44A")

    assert tender_id is None
    assert reference == "44A"


def test_title_link_is_kept_as_the_detail_url() -> None:
    rows = _parse_listing(LISTING_HTML, "high_value")

    assert rows[0].detail_url == "https://eprocure.gov.in/cppp/tendersfullview/MTQxMTcyOTk="
    # A row without a link keeps the field empty rather than raising.
    assert rows[1].detail_url is None


# --- classification -------------------------------------------------------
# Each case below is one the classifier previously got wrong.


def test_naming_a_structure_without_commissioning_work_is_not_construction() -> None:
    # "Cleaning of weigh bridges" is a services contract, not bridge work.
    category, is_construction = _classify("Regular cleaning of 8 nos weigh bridges")

    assert is_construction is False
    assert category is None


def test_substring_matches_do_not_fire() -> None:
    # "track" must not match "TRACKED", nor "rail" match "guardrail".
    _, is_construction = _classify("PROVN OF 01 X 20 TON TRACKED EXCAVATOR WITH BREAKER")

    assert is_construction is False


def test_goods_purchases_are_excluded_even_with_a_work_verb() -> None:
    _, is_construction = _classify("Supply of cement for construction of quarters")

    assert is_construction is False


def test_toll_operation_is_not_road_construction() -> None:
    _, is_construction = _classify("Engagement of user fee agency for 4-Lane Greenfield Expressway")

    assert is_construction is False


def test_genuine_work_is_categorised() -> None:
    category, is_construction = _classify("Construction of Major Bridge at Km 27 on NH-48")

    assert is_construction is True
    assert category == "Bridges"


def test_category_is_scored_not_first_match() -> None:
    # Mentions a building once and roads twice; the roads win rather than
    # whichever category happens to be checked first.
    category, _ = _classify(
        "Construction of approach roads and service road near the office building"
    )

    assert category == "Roads & Highways"


def test_unrecognised_structure_still_counts_as_work() -> None:
    category, is_construction = _classify("Construction of a helipad apron")

    assert is_construction is True
    assert category == "General Civil"
