"""The scorer's own correctness.

A scorer that is wrong about what counts produces numbers that look like
measurement but are not, so the comparison rules get tested directly: the
traps are booleans comparing equal to numbers, a float percentage surviving
a JSON round trip, and an abstention being counted as a failure to find.
"""

from __future__ import annotations

import pytest

from app.eval import CASES, CASES_BY_KEY, Verdict, render_pdf, score_fact, values_match
from app.eval.golden import NHAI_BYPASS
from app.eval.scoring import ExtractionReport, FactScore, normalise_text
from app.extraction.schema import FACTS_BY_KEY, FactType
from app.pipeline.parse import parse_pdf


def _score(key: str, actual: object, page: int | None = None) -> FactScore:
    return score_fact(
        key,
        actual,
        expected_facts=NHAI_BYPASS.expected_facts,
        expected_pages=NHAI_BYPASS.expected_pages,
        actual_page=page,
    )


class TestValueMatching:
    def test_money_must_be_exact(self) -> None:
        assert values_match(2_845_000_000, 2_845_000_000, FactType.MONEY)
        # A crore conversion off by a factor of ten is the classic failure,
        # and it must never score as close enough.
        assert not values_match(2_845_000_000, 284_500_000, FactType.MONEY)

    def test_percent_tolerates_float_round_trip(self) -> None:
        assert values_match(0.05, 0.05000000000000001, FactType.PERCENT)
        assert not values_match(0.05, 0.1, FactType.PERCENT)

    def test_boolean_does_not_accept_numbers(self) -> None:
        # bool is a subclass of int, so a naive equality check would score
        # True as matching 1 and pass a percentage field off as a boolean.
        assert values_match(True, True, FactType.BOOLEAN)
        assert not values_match(True, 1, FactType.BOOLEAN)
        assert not values_match(True, "yes", FactType.BOOLEAN)

    def test_numeric_field_does_not_accept_boolean(self) -> None:
        assert not values_match(5, True, FactType.PERCENT)
        assert not values_match(1, True, FactType.INTEGER)

    def test_text_matches_either_way_round(self) -> None:
        assert values_match("Class-I", "Class-I (Civil)", FactType.TEXT)
        assert values_match("Class-I (Civil)", "Class-I", FactType.TEXT)
        assert values_match("New Delhi", "new delhi", FactType.TEXT)
        assert not values_match("New Delhi", "Mumbai", FactType.TEXT)

    def test_empty_text_never_matches(self) -> None:
        assert not values_match("New Delhi", "", FactType.TEXT)
        assert not values_match("New Delhi", "   ", FactType.TEXT)

    def test_date_compares_iso_strings(self) -> None:
        assert values_match("2026-10-21", "2026-10-21", FactType.DATE)
        assert not values_match("2026-10-21", "2026-10-22", FactType.DATE)

    def test_null_matches_only_null(self) -> None:
        assert values_match(None, None, FactType.MONEY)
        assert not values_match(2_845_000_000, None, FactType.MONEY)

    def test_normalise_text_strips_punctuation_and_case(self) -> None:
        assert normalise_text("Class-I  (Civil)") == "class i civil"


class TestVerdicts:
    def test_right_value_is_correct(self) -> None:
        assert _score("value.estimated", 2_845_000_000).verdict is Verdict.CORRECT

    def test_wrong_value_is_wrong(self) -> None:
        assert _score("value.estimated", 1).verdict is Verdict.WRONG

    def test_null_is_missed_not_wrong(self) -> None:
        # The distinction drives the remedy: a miss is a retrieval problem,
        # a wrong value is a reading problem.
        assert _score("value.estimated", None).verdict is Verdict.MISSED

    def test_unannotated_key_is_unscored(self) -> None:
        assert _score("no.such.fact", 5).verdict is Verdict.UNSCORED

    def test_page_judged_only_when_value_found(self) -> None:
        assert _score("value.estimated", 2_845_000_000, page=1).page_correct is True
        assert _score("value.estimated", 2_845_000_000, page=3).page_correct is False
        # Nothing to judge: the value was never found, and counting a page
        # failure too would record one mistake twice.
        assert _score("value.estimated", None, page=None).page_correct is None


class TestRates:
    def _report(self, *scores: FactScore) -> ExtractionReport:
        return ExtractionReport(case_key="t", scores=scores)

    def test_accuracy_counts_misses_against_the_system(self) -> None:
        report = self._report(
            _score("value.estimated", 2_845_000_000),
            _score("emd.amount", None),
        )
        assert report.accuracy == 0.5

    def test_precision_excludes_abstentions(self) -> None:
        # Answering one fact correctly and declining the other is 100%
        # precise and 50% accurate. Reporting only one of those would hide
        # a real difference in behaviour.
        report = self._report(
            _score("value.estimated", 2_845_000_000),
            _score("emd.amount", None),
        )
        assert report.precision == 1.0

    def test_precision_falls_when_a_value_is_invented(self) -> None:
        report = self._report(
            _score("value.estimated", 2_845_000_000),
            _score("emd.amount", 999),
        )
        assert report.precision == 0.5
        assert report.accuracy == 0.5

    def test_unscored_facts_stay_out_of_the_denominator(self) -> None:
        report = self._report(
            _score("value.estimated", 2_845_000_000),
            _score("no.such.fact", 5),
        )
        assert len(report.scorable) == 1
        assert report.accuracy == 1.0

    def test_empty_report_does_not_divide_by_zero(self) -> None:
        report = self._report()
        assert report.accuracy == 0.0
        assert report.precision == 0.0
        assert report.page_accuracy == 0.0


class TestGoldenSet:
    def test_every_case_has_a_unique_key(self) -> None:
        assert len(CASES_BY_KEY) == len(CASES)

    @pytest.mark.parametrize("case", CASES, ids=lambda c: c.key)
    def test_expected_facts_are_real_fact_keys(self, case: object) -> None:
        unknown = set(case.expected_facts) - set(FACTS_BY_KEY)  # type: ignore[attr-defined]
        assert not unknown, f"not in the extraction schema: {sorted(unknown)}"

    @pytest.mark.parametrize("case", CASES, ids=lambda c: c.key)
    def test_annotated_pages_cover_annotated_facts(self, case: object) -> None:
        facts = set(case.expected_facts)  # type: ignore[attr-defined]
        pages = set(case.expected_pages)  # type: ignore[attr-defined]
        assert not pages - facts, "a page is annotated for a fact with no expected value"
        assert not facts - pages, "a fact has an expected value but no page"

    @pytest.mark.parametrize("case", CASES, ids=lambda c: c.key)
    def test_annotated_pages_exist_in_the_document(self, case: object) -> None:
        page_count = len(case.pages)  # type: ignore[attr-defined]
        for key, page in case.expected_pages.items():  # type: ignore[attr-defined]
            assert 1 <= page <= page_count, f"{key} cites page {page} of {page_count}"

    @pytest.mark.parametrize("case", CASES, ids=lambda c: c.key)
    def test_case_covers_the_whole_schema(self, case: object) -> None:
        # Not strictly required of a golden case, but true of the ones here,
        # and worth failing on: a fact added to the schema without an
        # annotation would otherwise be scored by nothing.
        missing = set(FACTS_BY_KEY) - set(case.expected_facts)  # type: ignore[attr-defined]
        assert not missing, f"unannotated facts: {sorted(missing)}"


class TestRenderedDocument:
    """The generated PDF has to actually say what the annotations claim.

    Without this the golden set could drift into scoring the extractor
    against text that is not in the document it reads.
    """

    def test_renders_the_expected_page_count(self) -> None:
        parsed = parse_pdf(render_pdf(NHAI_BYPASS))
        assert len(parsed.pages) == len(NHAI_BYPASS.pages)

    def test_has_a_text_layer_on_every_page(self) -> None:
        parsed = parse_pdf(render_pdf(NHAI_BYPASS))
        for page in parsed.pages:
            assert page.text.strip(), f"page {page.number} rendered with no extractable text"

    @pytest.mark.parametrize(
        ("needle", "page"),
        [
            ("284,50,00,000", 1),
            ("1,42,25,000", 1),
            ("21 October 2026", 2),
            ("24 months", 2),
            ("142,25,00,000", 3),
            ("Class-I", 3),
            ("0.05%", 4),
            ("New Delhi", 4),
        ],
    )
    def test_key_figures_land_on_their_annotated_page(self, needle: str, page: int) -> None:
        parsed = parse_pdf(render_pdf(NHAI_BYPASS))
        text = parsed.pages[page - 1].text
        assert needle in " ".join(text.split()), f"{needle!r} is not on page {page}"


class TestReproducibility:
    """The fixture has to render to identical bytes every time.

    Ingest identifies a document version by the hash of its bytes, so a PDF
    carrying a creation timestamp or a random trailer ID is a *different*
    document on every run. The corpus then fills with versions of the same
    file, and retrieval answers from whichever copy ranks first — which is
    how five facts came back with a page but no bounding box.
    """

    def test_two_renders_are_byte_identical(self) -> None:
        assert render_pdf(NHAI_BYPASS) == render_pdf(NHAI_BYPASS)

    def test_the_frozen_pdf_still_parses(self) -> None:
        # Blanking the trailer ID overwrites bytes in place; if it ever
        # changed the file's length, the cross-reference offsets would be
        # wrong and the document would not open at all.
        parsed = parse_pdf(render_pdf(NHAI_BYPASS))
        assert len(parsed.pages) == len(NHAI_BYPASS.pages)
        assert "284,50,00,000" in " ".join(parsed.pages[0].text.split())

    def test_freezing_preserves_length(self) -> None:
        from app.eval.golden import _freeze_document_id

        raw = b"trailer\n<</Size 21/ID[<ABCDEF01><99887766>]>>\nstartxref\n5719"
        frozen = _freeze_document_id(raw)
        assert len(frozen) == len(raw)
        assert b"<00000000><00000000>" in frozen

    def test_freezing_a_pdf_without_an_id_is_a_no_op(self) -> None:
        from app.eval.golden import _freeze_document_id

        raw = b"trailer\n<</Size 21/Root 1 0 R>>\nstartxref\n10"
        assert _freeze_document_id(raw) == raw
