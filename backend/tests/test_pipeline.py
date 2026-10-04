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
