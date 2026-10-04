"""Splitting a parsed document into retrievable passages.

Two constraints shape this. A chunk has to be small enough that retrieving it
puts the answer near the top of the context rather than buried in ten pages of
boilerplate, and large enough that a clause keeps the sentence that qualifies
it — tender terms are full of "subject to", "except where", "read with clause
14.2", and a chunk that drops the qualifier reads as the opposite of what the
document says.

So chunks are built from whole blocks, packed up to a token budget, with an
overlap carried between neighbours. Every chunk records the page range it came
from, because an answer that cannot be pointed at a page is not usable here.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import get_settings
from app.core.logging import get_logger
from app.pipeline.parse import ParsedDocument

logger = get_logger(__name__)

# Tokens are not counted exactly: the tokenizer that matters belongs to the
# embedding model, and calling it per candidate chunk would cost a network
# round trip each. English averages close to four characters per token, and
# the budget only needs to be approximately right.
_CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    content: str
    page_from: int
    page_to: int
    bbox: list[float] | None = None
    section_path: str | None = None
    is_table: bool = False


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
    """Pack page blocks into overlapping, page-attributed passages."""
    target_chars, overlap_chars = _budget_chars()

    chunks: list[Chunk] = []
    buffer = ""
    buffer_start_page = 1
    buffer_end_page = 1

    def flush() -> None:
        nonlocal buffer, buffer_start_page, buffer_end_page
        content = buffer.strip()
        if not content:
            buffer = ""
            return
        chunks.append(
            Chunk(
                ordinal=len(chunks),
                content=content,
                page_from=buffer_start_page,
                page_to=buffer_end_page,
            )
        )
        buffer = _tail(content, overlap_chars)
        buffer_start_page = buffer_end_page

    for page in parsed.pages:
        for block in page.blocks:
            if not buffer:
                buffer_start_page = page.number
            buffer_end_page = page.number

            candidate = f"{buffer}\n{block.text}" if buffer else block.text
            if len(candidate) > target_chars and buffer:
                flush()
                candidate = f"{buffer}\n{block.text}" if buffer else block.text
                buffer_start_page = page.number
            buffer = candidate

    flush()
    # The final flush leaves the overlap tail behind; it is already contained
    # in the chunk before it, so it is not emitted again.

    logger.info(
        "document_chunked",
        chunks=len(chunks),
        pages=parsed.page_count,
        target_tokens=get_settings().chunk_target_tokens,
    )
    return chunks
