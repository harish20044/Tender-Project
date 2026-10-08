"""Tests for the document pipeline: parsing, chunking, and answer assembly.

Chunking and citation handling are pure and are tested outright. Parsing needs
a real PDF, which is built here with PyMuPDF rather than committed as a binary
fixture, so the test states its own input.
"""

from __future__ import annotations

import pytest

from app.pipeline.answer import _CITATION_PATTERN, Answer
from app.pipeline.chunk import chunk_document
from app.pipeline.parse import ParsedDocument, ParsedPage, TextBlock, parse_pdf
from app.schemas.faqs import CATEGORIES, FAQS, FAQS_BY_KEY, faqs_for


def _page(number: int, *paragraphs: str) -> ParsedPage:
    blocks = [
        TextBlock(text=p, bbox=(0.0, float(i * 20), 500.0, float(i * 20 + 18)))
        for i, p in enumerate(paragraphs)
    ]
    return ParsedPage(number=number, text="\n".join(paragraphs), blocks=blocks)


# --- the fifty standard questions ------------------------------------------ #


def test_there_are_exactly_fifty_faqs_with_unique_keys() -> None:
    assert len(FAQS) == 50
    assert len({faq.key for faq in FAQS}) == 50
    assert len(FAQS_BY_KEY) == 50


def test_every_faq_is_a_question_in_a_known_category() -> None:
    for faq in FAQS:
        assert faq.question.endswith("?"), faq.key
        assert faq.category in CATEGORIES


def test_faqs_can_be_filtered_by_category() -> None:
    risk = faqs_for("Risk")
    assert risk
    assert all(faq.category == "Risk" for faq in risk)
    assert faqs_for(None) == FAQS


# --- chunking -------------------------------------------------------------- #


def test_chunks_carry_the_pages_they_came_from() -> None:
    parsed = ParsedDocument(pages=[_page(1, "alpha " * 50), _page(2, "beta " * 50)])

    chunks = chunk_document(parsed)

    assert chunks
    assert chunks[0].page_from == 1
    assert max(c.page_to for c in chunks) == 2
    for chunk in chunks:
        assert chunk.page_from <= chunk.page_to


def test_chunk_ordinals_are_contiguous_from_zero() -> None:
    parsed = ParsedDocument(pages=[_page(n, "clause text " * 120) for n in range(1, 5)])

    chunks = chunk_document(parsed)

    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_a_long_document_is_split_rather_than_returned_whole() -> None:
    parsed = ParsedDocument(pages=[_page(n, "eligibility criteria " * 200) for n in range(1, 4)])

    chunks = chunk_document(parsed)

    assert len(chunks) > 1


def test_an_empty_document_yields_no_chunks() -> None:
    assert chunk_document(ParsedDocument(pages=[])) == []


# --- parsing --------------------------------------------------------------- #


def _pdf_bytes(pages: list[str]) -> bytes:
    pymupdf = pytest.importorskip("pymupdf")
    document = pymupdf.open()
    for body in pages:
        page = document.new_page()
        page.insert_textbox(pymupdf.Rect(50, 50, 550, 750), body, fontsize=11, fontname="helv")
    data = document.tobytes()
    document.close()
    return bytes(data)


def test_parsing_keeps_one_entry_per_page_with_its_number() -> None:
    data = _pdf_bytes(["Earnest money deposit is Rs 5,00,000.", "Liquidated damages at 0.05%."])

    parsed = parse_pdf(data)

    assert parsed.page_count == 2
    assert [p.number for p in parsed.pages] == [1, 2]
    assert "Earnest money" in parsed.pages[0].text
    assert "Liquidated damages" in parsed.pages[1].text


def test_a_page_with_a_text_layer_is_not_flagged_for_ocr() -> None:
    data = _pdf_bytes(["The contract completion period is 24 months. " * 8])

    parsed = parse_pdf(data)

    assert parsed.needs_ocr is False
    assert parsed.scanned_pages == []


def test_a_page_with_almost_no_text_is_flagged_as_needing_ocr() -> None:
    data = _pdf_bytes(["x"])

    parsed = parse_pdf(data)

    assert parsed.needs_ocr is True
    assert parsed.scanned_pages == [1]


# --- citation parsing ------------------------------------------------------ #


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("The EMD is Rs 5 lakh [1].", {1}),
        ("Full-width brackets 【2】 are used by some models.", {2}),
        ("Parenthesised (3) also appears.", {3}),
        ("Decorated markers 【1†L6-L7】 keep their number.", {1}),
        ("Several at once [1] and [3].", {1, 3}),
        ("No citation at all.", set()),
    ],
)
def test_citation_markers_are_recognised_in_every_bracket_style(
    body: str, expected: set[int]
) -> None:
    assert {int(m) for m in _CITATION_PATTERN.findall(body)} == expected


def test_an_unanswerable_answer_carries_no_citations() -> None:
    answer = Answer(text="not stated", is_answerable=False)

    assert answer.citations == []
    assert answer.confidence == 0.0


# --- page attribution ------------------------------------------------------ #


def _multipage() -> ParsedDocument:
    """Two pages whose text is distinct enough to tell apart by content."""
    return ParsedDocument(
        pages=[
            _page(1, "Earnest money deposit is Rs 5,00,000 payable by bank guarantee. " * 12),
            _page(2, "Liquidated damages accrue at 0.05 percent per day of delay. " * 12),
        ]
    )


def test_a_chunk_knows_which_page_each_offset_belongs_to() -> None:
    chunks = chunk_document(_multipage())

    for chunk in chunks:
        assert chunk.page_offsets
        assert chunk.page_at(0) == chunk.page_offsets[0][0]


def test_a_quote_is_attributed_to_its_own_page_not_the_chunk_start() -> None:
    chunks = chunk_document(_multipage())

    pages = {
        probe: next((page for chunk in chunks if (page := chunk.page_of(probe)) is not None), None)
        for probe in ("Earnest money deposit", "Liquidated damages accrue")
    }

    assert pages["Earnest money deposit"] == 1
    assert pages["Liquidated damages accrue"] == 2


def test_a_quote_absent_from_a_chunk_resolves_to_no_page() -> None:
    chunk = chunk_document(_multipage())[0]

    assert chunk.page_of("a clause that appears nowhere in this document") is None
    assert chunk.page_of("") is None


def test_chunks_prefer_to_break_at_page_boundaries() -> None:
    # Pages large enough to stand alone should not be stitched together, since
    # a chunk spanning pages can only cite a range.
    parsed = ParsedDocument(pages=[_page(n, "clause text " * 300) for n in range(1, 4)])

    chunks = chunk_document(parsed)

    assert any(not chunk.spans_pages for chunk in chunks)


class TestRegionGeometry:
    """A quote resolving to a rectangle on the page it was printed on.

    The page size travels with the box because a box in PDF points means
    nothing to a viewer that has drawn the page at some arbitrary width, and
    tender packs mix A4 portrait pages with A3 landscape drawings.
    """

    def _chunks(self) -> list:
        from app.eval.golden import NHAI_BYPASS, render_pdf
        from app.pipeline.chunk import chunk_document
        from app.pipeline.parse import parse_pdf

        return chunk_document(parse_pdf(render_pdf(NHAI_BYPASS)))

    def _region(self, quote: str):
        for chunk in self._chunks():
            region = chunk.region_of(quote)
            if region is not None:
                return region
        return None

    def test_a_quote_resolves_to_a_region_on_its_own_page(self) -> None:
        region = self._region("Liquidated Damages: Liquidated damages for delay")
        assert region is not None
        assert region.page == 4
        assert region.page_width > 0 and region.page_height > 0

    def test_distinct_clauses_get_distinct_regions(self) -> None:
        damages = self._region("Liquidated Damages: Liquidated damages for delay")
        arbitration = self._region("Dispute Resolution: Disputes shall first be referred")
        assert damages is not None and arbitration is not None
        # Same page, different places on it. A single box covering the whole
        # page would satisfy "has a region" while telling nobody anything.
        assert damages.page == arbitration.page
        assert damages.bbox[1] != arbitration.bbox[1]

    def test_relative_coordinates_are_fractions_of_the_page(self) -> None:
        region = self._region("Estimated Contract Value")
        assert region is not None
        relative = region.relative
        assert relative is not None
        assert all(0.0 <= value <= 1.0 for value in relative)
        # x0 < x1 and y0 < y1, or the rectangle is inside out.
        assert relative[0] < relative[2]
        assert relative[1] < relative[3]

    def test_relative_is_none_without_a_page_size(self) -> None:
        from app.pipeline.chunk import Region

        region = Region(page=1, bbox=[0, 0, 10, 10], page_width=0.0, page_height=0.0)
        assert region.relative is None

    def test_a_quote_not_in_the_chunk_has_no_region(self) -> None:
        assert self._region("this sentence appears in no tender anywhere") is None


class TestElidedQuoteMatching:
    """A model quoting non-contiguously must still be locatable.

    Asked for the clause naming the arbitration seat, the model returned
    "Dispute Resolution: Unresolved disputes shall be settled by arbitration
    ... seated at New Delhi" — joining the heading to a later sentence and
    dropping the one between. No leading run of that exists in the document,
    so the match failed, the fact fell back to the page the model *claimed*
    (wrong), and it lost its region entirely. This is ordinary LLM quoting
    behaviour, so the matcher has to survive it.
    """

    SOURCE = (
        "Dispute Resolution: Disputes shall first be referred to the Dispute Resolution\n"
        "Board. Unresolved disputes shall be settled by arbitration under the\n"
        "Arbitration and Conciliation Act, 1996, seated at New Delhi."
    )

    def test_an_elided_quote_is_located(self) -> None:
        from app.pipeline.chunk import span_of

        quote = (
            "Dispute Resolution: Unresolved disputes shall be settled by arbitration "
            "under the Arbitration and Conciliation Act, 1996, seated at New Delhi."
        )
        start, end = span_of(quote, self.SOURCE)
        assert start >= 0, "an elided quote must still pin to the source"
        # It must land on the surviving sentence, not on the heading it was
        # spliced onto — landing on the heading would cite the wrong clause.
        assert "Unresolved disputes" in self.SOURCE[start:end]

    def test_an_exact_quote_still_matches_from_the_front(self) -> None:
        from app.pipeline.chunk import span_of

        quote = "Disputes shall first be referred to the Dispute Resolution Board."
        start, _ = span_of(quote, self.SOURCE)
        assert self.SOURCE[start:].startswith("Disputes shall first")

    def test_a_paraphrased_tail_matches_on_the_leading_run(self) -> None:
        from app.pipeline.chunk import span_of

        quote = "Unresolved disputes shall be settled by arbitration under something else entirely"
        start, _ = span_of(quote, self.SOURCE)
        assert start >= 0

    def test_text_that_is_absent_still_returns_not_found(self) -> None:
        from app.pipeline.chunk import span_of

        # The loosened matching must not start finding things that are not
        # there — that would attribute facts to arbitrary pages.
        assert span_of("liquidated damages for delay shall be levied", self.SOURCE) == (-1, -1)

    def test_a_too_short_quote_is_not_matched_loosely(self) -> None:
        from app.pipeline.chunk import span_of

        # Two words of boilerplate appear everywhere; matching on them would
        # be worse than reporting nothing.
        assert span_of("shall be", self.SOURCE)[0] >= 0  # present verbatim
        assert span_of("utterly absent", self.SOURCE) == (-1, -1)

    def test_empty_quote_is_not_found(self) -> None:
        from app.pipeline.chunk import span_of

        assert span_of("", self.SOURCE) == (-1, -1)
        assert span_of("   ", self.SOURCE) == (-1, -1)


class TestStrictBeforeLoose:
    """An exact match must outrank an elision-tolerant one.

    The sliding window is a weaker claim and it collides: "of not less than
    Rs." appears both in a net-worth clause and in an insurance clause pages
    apart. Searching passage by passage with elision allowed attributed the
    net-worth figure to whichever passage happened to rank first, which is
    how fixing one citation broke another.
    """

    NET_WORTH = (
        "(b) The Bidder shall have a positive Net Worth of not less than\n"
        "Rs. 28,45,00,000 as at the close of the preceding financial year."
    )
    INSURANCE = "Third Party Liability insurance of not less than Rs. 5,00,00,000 per occurrence."

    QUOTE = (
        "The Bidder shall have a positive Net Worth of not less than "
        "Rs. 28,45,00,000 as at the close of the preceding financial year."
    )

    def test_strict_mode_refuses_the_colliding_window(self) -> None:
        from app.pipeline.chunk import span_of

        # The insurance clause shares only a short window with the quote, so
        # strict matching must find nothing in it.
        assert span_of(self.QUOTE, self.INSURANCE, allow_elided=False) == (-1, -1)

    def test_loose_mode_would_have_matched_it(self) -> None:
        from app.pipeline.chunk import span_of

        # This is the collision the two-phase search exists to avoid: on its
        # own, loose matching does hit the wrong clause.
        assert span_of(self.QUOTE, self.INSURANCE, allow_elided=True)[0] >= 0

    def test_strict_mode_still_finds_the_real_clause(self) -> None:
        from app.pipeline.chunk import span_of

        assert span_of(self.QUOTE, self.NET_WORTH, allow_elided=False)[0] >= 0

    def test_strict_mode_rejects_an_elided_quote(self) -> None:
        from app.pipeline.chunk import span_of

        source = (
            "Dispute Resolution: Disputes shall first be referred to the Board. "
            "Unresolved disputes shall be settled by arbitration seated at New Delhi."
        )
        elided = (
            "Dispute Resolution: Unresolved disputes shall be settled by "
            "arbitration seated at New Delhi."
        )
        # Strict finds nothing, which is what sends the search to its second
        # pass rather than guessing.
        assert span_of(elided, source, allow_elided=False) == (-1, -1)
        assert span_of(elided, source, allow_elided=True)[0] >= 0
