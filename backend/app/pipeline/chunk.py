"""Splitting a parsed document into retrievable passages.

Three constraints shape this. A chunk has to be small enough that retrieving
it puts the answer near the top of the context rather than buried in ten pages
of boilerplate; large enough that a clause keeps the sentence that qualifies it
— tender terms are full of "subject to", "except where", "read with clause
14.2", and a chunk that drops the qualifier reads as the opposite of what the
document says; and attributable to a *page*, because a citation an estimator
cannot turn to is not a citation.

The third constraint is why chunks break at page boundaries by default. A
chunk that spans pages 1 to 3 can only cite "pp. 1-3", which is the difference
between a reference and a gesture. Where a page is too small to fill a chunk
on its own the buffer is allowed to continue across the break, and the offset
each page starts at is recorded so a quote can still be pinned to the exact
page it came from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.core.logging import get_logger
from app.pipeline.parse import ParsedDocument

logger = get_logger(__name__)

# Tokens are not counted exactly: the tokenizer that matters belongs to the
# embedding model, and calling it per candidate chunk would cost a network
# round trip each. English averages close to four characters per token, and
# the budget only needs to be approximately right.
_CHARS_PER_TOKEN = 4

# A page break ends the chunk once it holds at least this share of the target.
# Below it, ending the chunk would emit a fragment too small to carry context,
# so the buffer continues and the page offsets below keep it citable.
_PAGE_BREAK_MIN_FILL = 0.35


def span_of(quote: str, text: str) -> tuple[int, int]:
    """Where ``quote`` starts and ends in ``text``, or (-1, -1).

    Matched against the raw text with whitespace treated as elastic, rather
    than by normalising both sides and scaling the result back: a quote that
    crosses a line break differs from the source by exactly the characters
    that normalising removes, and the resulting drift is enough to put an
    offset on the wrong side of a page boundary.

    Only the first twelve words are matched, which is long enough to be
    unambiguous and short enough to survive the model paraphrasing the tail
    of a long quote. The end returned is therefore the end of the matched
    prefix, not of the whole quote — good enough to pick out which blocks a
    citation covers, which is all it is used for.
    """
    words = quote.split()[:12]
    if not words:
        return -1, -1
    pattern = re.compile(r"\s+".join(re.escape(word) for word in words), re.IGNORECASE)
    match = pattern.search(text)
    return (match.start(), match.end()) if match else (-1, -1)


def offset_of(quote: str, text: str) -> int:
    """Where ``quote`` starts in ``text``, or -1."""
    return span_of(quote, text)[0]


@dataclass(frozen=True)
class BlockSpan:
    """A laid-out block of the source page, and where its text landed.

    ``start`` and ``end`` are offsets into the chunk's own content, so a
    quote located in the chunk can be mapped back to the region of the page
    it was printed in.
    """

    start: int
    end: int
    page: int
    bbox: tuple[float, float, float, float]
    # The page's own size in PDF points. Carried alongside the box because
    # the box means nothing without it: a highlight has to know what the
    # coordinates are a fraction of, and a pack can mix A4 portrait pages
    # with A3 landscape drawings in one document.
    page_width: float = 0.0
    page_height: float = 0.0

    def overlaps(self, start: int, end: int) -> bool:
        return self.start < end and start < self.end


@dataclass(frozen=True)
class Region:
    """Where a quote sits: which page, and where on it.

    The page size travels with the box rather than being looked up
    separately, because the two are only meaningful together and fetching
    them by different routes is how they come to disagree.
    """

    page: int
    bbox: list[float]
    page_width: float
    page_height: float

    @property
    def relative(self) -> list[float] | None:
        """The box as fractions of the page, or None if the size is unknown.

        What a highlight overlay actually wants: the renderer knows how big
        it has drawn the page, not how big the page is in points.
        """
        if self.page_width <= 0 or self.page_height <= 0:
            return None
        x0, y0, x1, y1 = self.bbox
        return [
            x0 / self.page_width,
            y0 / self.page_height,
            x1 / self.page_width,
            y1 / self.page_height,
        ]


def union_bbox(boxes: list[tuple[float, float, float, float]]) -> list[float] | None:
    """The smallest box covering all of ``boxes``.

    A quote usually spans two or three blocks — a clause heading and the
    sentence under it — and one box around the lot is what a highlight
    overlay needs. The union is loose where blocks sit in separate columns,
    which tender text rarely does.
    """
    if not boxes:
        return None
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    content: str
    page_from: int
    page_to: int
    # (page number, offset in `content` where that page's text begins), so a
    # quote found at an offset can be attributed to the page it truly sits on
    # rather than to the first page of the range.
    page_offsets: list[tuple[int, int]] = field(default_factory=list)
    # Where each source block's text landed in `content`, so a quote can be
    # resolved to the region of the page it was printed in rather than only
    # to the page.
    blocks: list[BlockSpan] = field(default_factory=list)
    bbox: list[float] | None = None
    section_path: str | None = None
    is_table: bool = False

    @property
    def spans_pages(self) -> bool:
        return self.page_to > self.page_from

    def page_at(self, offset: int) -> int:
        """Which page the text at ``offset`` came from."""
        if offset < 0:
            return self.page_from
        page = self.page_from
        for number, start in self.page_offsets:
            if offset >= start:
                page = number
            else:
                break
        return page

    def page_of(self, quote: str) -> int | None:
        """Which page a quote came from, or None if it is not in this chunk."""
        if not quote:
            return None
        offset = offset_of(quote, self.content)
        return self.page_at(offset) if offset >= 0 else None

    def region_of(self, quote: str) -> Region | None:
        """Where on the page a quote was printed, or None.

        Restricted to blocks on the page the quote *starts* on. A quote
        running across a page break would otherwise union boxes from two
        different pages into one meaningless rectangle, and the citation
        already reports that single starting page.
        """
        if not quote or not self.blocks:
            return None
        start, end = span_of(quote, self.content)
        if start < 0:
            return None
        page = self.page_at(start)
        covering = [b for b in self.blocks if b.page == page and b.overlaps(start, end)]
        box = union_bbox([b.bbox for b in covering])
        if box is None:
            return None
        first = covering[0]
        return Region(
            page=page,
            bbox=box,
            page_width=first.page_width,
            page_height=first.page_height,
        )

    def bbox_of(self, quote: str) -> list[float] | None:
        """Just the rectangle, for callers that do not need the page size."""
        region = self.region_of(quote)
        return region.bbox if region else None


def _budget_chars() -> tuple[int, int]:
    settings = get_settings()
    return (
        settings.chunk_target_tokens * _CHARS_PER_TOKEN,
        settings.chunk_overlap_tokens * _CHARS_PER_TOKEN,
    )


def _tail(text: str, overlap_chars: int) -> str:
    """The end of a chunk, cut at a sentence boundary where one is near.

    Overlap exists so a clause split across two chunks survives in at least
    one of them whole. Cutting mid-sentence would defeat that, so the split
    prefers the last sentence end inside the overlap window.
    """
    if len(text) <= overlap_chars:
        return text
    window = text[-overlap_chars:]
    for separator in (". ", ".\n", "; ", "\n\n"):
        position = window.find(separator)
        if position != -1:
            return window[position + len(separator) :]
    return window


def chunk_document(parsed: ParsedDocument) -> list[Chunk]:
    """Pack page blocks into page-attributed passages."""
    target_chars, overlap_chars = _budget_chars()
    page_break_floor = int(target_chars * _PAGE_BREAK_MIN_FILL)

    chunks: list[Chunk] = []
    buffer = ""
    start_page = 1
    end_page = 1
    offsets: list[tuple[int, int]] = []
    spans: list[BlockSpan] = []

    def flush() -> None:
        nonlocal buffer, start_page, end_page, offsets, spans
        content = buffer.strip()
        if not content:
            buffer, offsets, spans = "", [], []
            return

        # strip() moved the content; shift recorded offsets to match.
        shift = len(buffer) - len(buffer.lstrip())
        adjusted = [
            (page, max(0, offset - shift))
            for page, offset in offsets
            if offset - shift < len(content)
        ]
        adjusted_spans = [
            BlockSpan(
                start=max(0, span.start - shift),
                end=min(len(content), span.end - shift),
                page=span.page,
                bbox=span.bbox,
                page_width=span.page_width,
                page_height=span.page_height,
            )
            for span in spans
            if span.end - shift > 0 and span.start - shift < len(content)
        ]

        chunks.append(
            Chunk(
                ordinal=len(chunks),
                content=content,
                page_from=start_page,
                page_to=end_page,
                page_offsets=adjusted or [(start_page, 0)],
                blocks=adjusted_spans,
            )
        )

        buffer = _tail(content, overlap_chars)
        start_page = end_page
        # The carried tail belongs to the page the chunk ended on.
        offsets = [(end_page, 0)] if buffer else []
        # Carry the spans covering the tail too, rebased onto it, so a quote
        # answered from the overlap still resolves to a region rather than
        # losing its geometry the moment it straddles a chunk boundary.
        if buffer:
            tail_start = len(content) - len(buffer)
            spans = [
                BlockSpan(
                    start=max(0, span.start - tail_start),
                    end=min(len(buffer), span.end - tail_start),
                    page=span.page,
                    bbox=span.bbox,
                    page_width=span.page_width,
                    page_height=span.page_height,
                )
                for span in adjusted_spans
                if span.end > tail_start
            ]
        else:
            spans = []

    for page in parsed.pages:
        if not page.blocks:
            continue

        # A page break is the preferred place to end a chunk, so long as what
        # is already buffered is substantial enough to stand alone.
        if buffer and len(buffer) >= page_break_floor:
            flush()

        if not buffer:
            start_page = page.number
            offsets = [(page.number, 0)]
        elif page.number != end_page:
            offsets.append((page.number, len(buffer) + 1))

        end_page = page.number

        for block in page.blocks:
            candidate = f"{buffer}\n{block.text}" if buffer else block.text
            if len(candidate) > target_chars and buffer:
                flush()
                if not buffer:
                    start_page = page.number
                    offsets = [(page.number, 0)]
                candidate = f"{buffer}\n{block.text}" if buffer else block.text
            # Where this block's text sits in the candidate buffer: at the end,
            # after the joining newline where one was added.
            block_start = len(candidate) - len(block.text)
            spans.append(
                BlockSpan(
                    start=block_start,
                    end=len(candidate),
                    page=page.number,
                    bbox=block.bbox,
                    page_width=page.width,
                    page_height=page.height,
                )
            )
            buffer = candidate

    flush()

    spanning = sum(1 for chunk in chunks if chunk.spans_pages)
    logger.info(
        "document_chunked",
        chunks=len(chunks),
        pages=parsed.page_count,
        spanning_pages=spanning,
        target_tokens=get_settings().chunk_target_tokens,
    )
    return chunks
