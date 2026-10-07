"""Answering a question from retrieved passages, with citations.

The model is given the passages and told to answer from them alone. That is
not a stylistic preference: a bid decision rests on these answers, and a
fluent invention about an EMD figure or a completion deadline is worse than
no answer at all. So the prompt requires a citation for every claim, and
requires the model to say when the passages do not contain the answer.

"Not answerable" is a first-class outcome, recorded as such, because a tender
genuinely may not state a thing — and the interface saying so is useful,
where a confident guess is a liability.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.core.logging import get_logger
from app.pipeline.retrieve import Passage
from app.providers.base import ChatMessage, ChatProvider

logger = get_logger(__name__)

_SYSTEM = """You answer questions about a construction tender using only the \
numbered extracts provided.

Rules:
- Use only what the extracts say. Never rely on outside knowledge or inference \
beyond them.
- Cite the extract number for every factual claim, like [2].
- Quote exact figures, dates and clause references as they appear.
- If the extracts do not contain the answer, reply exactly: NOT_FOUND
- Be concise. No preamble, no restating the question."""

NOT_FOUND = "NOT_FOUND"


def _max_passage_chars() -> int:
    """Never truncate below a whole chunk.

    Chunks are already sized to a token budget; trimming them again here
    silently drops the tail of the longest ones, which is where a clause that
    was packed last — a liquidated-damages rate, say — quietly disappears and
    the answer comes back as "not stated".
    """
    settings = get_settings()
    return (settings.chunk_target_tokens + settings.chunk_overlap_tokens) * 4


# Models are inconsistent about citation brackets: ASCII [2], the CJK full
# width 【2】 that some emit unprompted, and occasionally (2). All three mean
# the same thing, and missing one means falling back to citing everything,
# which makes the citation list worthless as evidence.
# Trailing content inside the bracket is tolerated because models sometimes
# decorate a marker with line hints, as in 【1†L6-L7】; the leading number is
# the part that identifies the passage.
_CITATION_PATTERN = re.compile(r"[\[\(【]\s*(\d{1,2})\s*(?:[^\]\)】]*)?[\]\)】]")

# Sentence-ish boundaries. Good enough to find a run of the answer long enough
# to locate in the source; it is not trying to be a sentence tokenizer.
_SENTENCE_SPLIT = re.compile(r"(?<=[.;:])\s+|\n+")


@dataclass
class Answer:
    text: str
    is_answerable: bool
    citations: list[dict[str, object]] = field(default_factory=list)
    confidence: float = 0.0

    @property
    def as_citation_payload(self) -> list[dict[str, object]]:
        return self.citations


def _format_passages(passages: list[Passage]) -> str:
    parts = []
    for index, passage in enumerate(passages, start=1):
        body = passage.content[: _max_passage_chars()]
        parts.append(f"[{index}] ({passage.citation})\n{body}")
    return "\n\n".join(parts)


def _confidence(passages: list[Passage], answered: bool) -> float:
    """A deliberately blunt confidence signal.

    It reflects how much support was retrieved, not the model's own certainty
    — models are poorly calibrated about that, and a number derived from the
    retrieval is at least something the system can reason about honestly.
    """
    if not answered or not passages:
        return 0.0
    return min(1.0, 0.35 + 0.1 * len(passages))


def _quoted_from(body: str, passage: Passage) -> str | None:
    """The longest run of the answer that appears verbatim in the passage.

    Models quote figures and clause wording directly far more often than they
    paraphrase them, so the overlap is usually enough to locate the sentence —
    and with it the page — without asking the model for a page it would only
    guess at.
    """
    haystack = " ".join(passage.content.split()).lower()
    best: str | None = None
    for sentence in _SENTENCE_SPLIT.split(body):
        cleaned = " ".join(sentence.split())
        if len(cleaned) < 25:
            continue
        probe = re.sub(r"[\[\(【]\s*\d{1,2}[^\]\)】]*[\]\)】]", "", cleaned).strip()
        if (
            len(probe) >= 25
            and probe.lower()[:60] in haystack
            and (best is None or len(probe) > len(best))
        ):
            best = probe
    return best


async def answer_question(
    question: str,
    passages: list[Passage],
    *,
    chat: ChatProvider,
    max_tokens: int = 900,
) -> Answer:
    """Answer from ``passages`` alone, or report that they do not contain it."""
    if not passages:
        return Answer(
            text="No relevant passage was found in this tender.",
            is_answerable=False,
        )

    messages = [
        ChatMessage(role="system", content=_SYSTEM),
        ChatMessage(
            role="user",
            content=f"Extracts:\n\n{_format_passages(passages)}\n\nQuestion: {question}",
        ),
    ]
    completion = await chat.complete(messages, max_tokens=max_tokens)
    body = (completion.text or "").strip()

    answered = bool(body) and NOT_FOUND not in body.upper()
    if not answered:
        return Answer(
            text="This tender does not state the answer to that question.",
            is_answerable=False,
            citations=[],
            confidence=0.0,
        )

    # Only passages the model actually cited are recorded, so a citation list
    # is evidence of what the answer rests on rather than a list of whatever
    # retrieval happened to return.
    referenced = {
        int(match) for match in _CITATION_PATTERN.findall(body) if 1 <= int(match) <= len(passages)
    }
    cited = sorted(referenced) or list(range(1, len(passages) + 1))

    # Where the answer quotes the document, the citation narrows to the page
    # that quote sits on rather than naming the passage's whole page range.
    citations = []
    for index in cited:
        passage = passages[index - 1]
        quoted = _quoted_from(body, passage)
        page = passage.page_of(quoted) if quoted else None
        # Where the quote can be located, so can the region it was printed
        # in — the same geometry the workspace draws for an extracted fact,
        # so an answer and a finding cite the document the same way.
        region = passage.region_of(quoted) if quoted else None
        citations.append(
            {
                "marker": index,
                "chunk_id": passage.chunk_id,
                "document_id": passage.document_id,
                "filename": passage.filename,
                "page_from": page or passage.page_from,
                "page_to": page or passage.page_to,
                "citation": f"{passage.filename} p.{page}" if page else passage.citation,
                "bbox": region.bbox if region else None,
                "bbox_relative": region.relative if region else None,
            }
        )

    logger.info("question_answered", question=question[:70], citations=len(citations))
    return Answer(
        text=body,
        is_answerable=True,
        citations=citations,
        confidence=_confidence(passages, answered),
    )
