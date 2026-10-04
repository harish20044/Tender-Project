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


def offset_of(quote: str, text: str) -> int:
    """Where ``quote`` starts in ``text``, or -1.

    Matched against the raw text with whitespace treated as elastic, rather
    than by normalising both sides and scaling the result back: a quote that
    crosses a line break differs from the source by exactly the characters
    that normalising removes, and the resulting drift is enough to put an
    offset on the wrong side of a page boundary.
    """
    words = quote.split()[:12]
    if not words:
        return -1
    pattern = re.compile(r"\s+".join(re.escape(word) for word in words), re.IGNORECASE)
    match = pattern.search(text)
    return match.start() if match else -1


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

    def flush() -> None:
        nonlocal buffer, start_page, end_page, offsets
        content = buffer.strip()
        if not content:
            buffer, offsets = "", []
            return

        # strip() moved the content; shift recorded offsets to match.
        shift = len(buffer) - len(buffer.lstrip())
        adjusted = [
            (page, max(0, offset - shift))
            for page, offset in offsets
            if offset - shift < len(content)
        ]

        chunks.append(
            Chunk(
                ordinal=len(chunks),
                content=content,
                page_from=start_page,
                page_to=end_page,
                page_offsets=adjusted or [(start_page, 0)],
            )
        )

        buffer = _tail(content, overlap_chars)
        start_page = end_page
        # The carried tail belongs to the page the chunk ended on.
        offsets = [(end_page, 0)] if buffer else []

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
