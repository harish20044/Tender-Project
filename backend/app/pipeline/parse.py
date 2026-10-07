"""Turning an uploaded PDF into text that keeps its page numbers.

Provenance is the point. Every answer this system gives has to be traceable
to a page of the source document, so text is never flattened into one blob —
it is carried per page from here all the way to the citation the interface
shows.

PyMuPDF does the extraction because it is fast and returns per-block
geometry. A page with no text layer is reported as such rather than silently
yielding an empty string, so the caller can tell "this page is blank" from
"this page is a scan we cannot read yet".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class TextBlock:
    """One laid-out run of text, with where it sits on the page."""

    text: str
    # [x0, y0, x1, y1] in PDF points, as the viewer's highlight overlay needs.
    bbox: tuple[float, float, float, float]


@dataclass
class ParsedPage:
    number: int  # 1-based, as printed and as cited
    text: str
    blocks: list[TextBlock] = field(default_factory=list)
    # The page's own size in PDF points. Carried because a block's bbox is
    # meaningless without it: anything drawing a highlight has to know what
    # the coordinates are a fraction of, and tender packs mix A4 portrait
    # drawings with A3 landscape ones in the same document.
    width: float = 0.0
    height: float = 0.0

    @property
    def is_scanned(self) -> bool:
        """Too little extractable text to be a real text layer.

        A scanned page yields a handful of stray characters at most, so the
        threshold separates it from a genuinely near-empty page such as a
        section divider.
        """
        return len(self.text.strip()) < get_settings().ocr_text_threshold_chars


@dataclass
class ParsedDocument:
    pages: list[ParsedPage]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def scanned_pages(self) -> list[int]:
        return [page.number for page in self.pages if page.is_scanned]

    @property
    def needs_ocr(self) -> bool:
        return bool(self.scanned_pages)

    @property
    def text(self) -> str:
        return "\n\n".join(page.text for page in self.pages)


def parse_pdf(data: bytes) -> ParsedDocument:
    """Extract text per page, keeping block geometry for citations."""
    import pymupdf

    settings = get_settings()
    pages: list[ParsedPage] = []

    # PyMuPDF ships no type information for its constructor.
    with pymupdf.open(stream=data, filetype="pdf") as document:  # type: ignore[no-untyped-call]
        if document.page_count > settings.max_document_pages:
            logger.warning(
                "document_over_page_budget",
                pages=document.page_count,
                budget=settings.max_document_pages,
            )

        for index, page in enumerate(document, start=1):
            if index > settings.max_document_pages:
                break

            blocks: list[TextBlock] = []
            for block in page.get_text("blocks"):
                x0, y0, x1, y1, text = block[0], block[1], block[2], block[3], block[4]
                cleaned = str(text).strip()
                if cleaned:
                    blocks.append(
                        TextBlock(text=cleaned, bbox=(float(x0), float(y0), float(x1), float(y1)))
                    )

            pages.append(
                ParsedPage(
                    number=index,
                    text="\n".join(block.text for block in blocks),
                    blocks=blocks,
                    width=float(page.rect.width),
                    height=float(page.rect.height),
                )
            )

    parsed = ParsedDocument(pages=pages)
    logger.info(
        "pdf_parsed",
        pages=parsed.page_count,
        scanned_pages=len(parsed.scanned_pages),
        characters=len(parsed.text),
    )
    return parsed
