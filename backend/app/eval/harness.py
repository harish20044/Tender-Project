"""Running a golden case through the real pipeline and scoring the result.

This deliberately uses the production path — the same ingest, the same
retrieval, the same extraction prompts — rather than calling the model
directly. A harness that bypasses chunking and retrieval measures the prompt,
not the system, and the two have come apart before: retrieval once returned
passages truncated below the chunk size, which no prompt-level test would
have caught.

The cost is that a run needs the database and both providers, and spends real
tokens. That is why it is a script rather than part of the test suite.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.db import models
from app.eval.golden import CASES_BY_KEY, GoldenCase, render_pdf
from app.eval.scoring import ExtractionReport, score_fact
from app.extraction.extract import extract_tender
from app.pipeline.answer import answer_question
from app.pipeline.ingest import ingest_pdf
from app.pipeline.retrieve import retrieve
from app.providers import GroqChatProvider, JinaProvider
from app.storage import get_storage


@dataclass
class AbstentionReport:
    """Whether the system declines questions the document cannot answer.

    Tracked separately from extraction because it is the failure that would
    most damage trust in the tool: a confident answer to a question the pack
    never addresses is worse than no answer, and an estimator has no way to
    catch it short of reading the pack themselves.
    """

    questions: tuple[str, ...] = ()
    abstained: tuple[str, ...] = ()
    answered: tuple[tuple[str, str], ...] = ()  # (question, the invented answer)

    @property
    def rate(self) -> float:
        return len(self.abstained) / len(self.questions) if self.questions else 0.0


@dataclass
class CaseResult:
    case_key: str
    tender_id: str
    pages: int
    chunks: int
    extraction: ExtractionReport
    abstention: AbstentionReport = field(default_factory=AbstentionReport)


async def ingest_case(case: GoldenCase) -> tuple[str, int, int]:
    """Put the case's document through the normal ingest path."""
    result = await ingest_pdf(
        render_pdf(case),
        filename=f"{case.key}-golden.pdf",
        reference=case.reference,
        title=case.title,
        kind=models.DocumentKind.NIT,
        storage=get_storage(),
        embedder=JinaProvider(),
    )
    return result.tender_id, result.pages, result.chunks


async def score_extraction(case: GoldenCase, tender_id: str) -> ExtractionReport:
    facts = await extract_tender(tender_id, chat=GroqChatProvider(), embedder=JinaProvider())
    found = {fact.key: fact for fact in facts}

    scores = []
    # Iterate the annotations, not the results: a fact the extractor never
    # returned at all has to score as missed rather than vanish from the
    # denominator, which is what iterating `found` would do.
    for key in case.expected_facts:
        fact = found.get(key)
        scores.append(
            score_fact(
                key,
                fact.value if fact else None,
                expected_facts=case.expected_facts,
                expected_pages=case.expected_pages,
                actual_page=fact.page if fact else None,
                confidence=fact.confidence if fact else 0.0,
                actual_bbox=fact.bbox if fact else None,
            )
        )
    return ExtractionReport(case_key=case.key, scores=tuple(scores))


async def score_abstention(case: GoldenCase, tender_id: str) -> AbstentionReport:
    if not case.unanswerable:
        return AbstentionReport()

    embedder, chat = JinaProvider(), GroqChatProvider()
    abstained: list[str] = []
    answered: list[tuple[str, str]] = []

    for question in case.unanswerable:
        passages = await retrieve(question, tender_id=tender_id, embedder=embedder, limit=8)
        answer = await answer_question(question, passages, chat=chat)
        if answer.is_answerable:
            answered.append((question, answer.text))
        else:
            abstained.append(question)

    return AbstentionReport(
        questions=case.unanswerable,
        abstained=tuple(abstained),
        answered=tuple(answered),
    )


async def run_case(case: GoldenCase, *, check_abstention: bool = True) -> CaseResult:
    tender_id, pages, chunks = await ingest_case(case)
    extraction = await score_extraction(case, tender_id)
    abstention = await score_abstention(case, tender_id) if check_abstention else AbstentionReport()
    return CaseResult(
        case_key=case.key,
        tender_id=tender_id,
        pages=pages,
        chunks=chunks,
        extraction=extraction,
        abstention=abstention,
    )


async def run_all(
    keys: list[str] | None = None, *, check_abstention: bool = True
) -> list[CaseResult]:
    selected = [CASES_BY_KEY[k] for k in keys] if keys else list(CASES_BY_KEY.values())
    return [await run_case(case, check_abstention=check_abstention) for case in selected]


def run_all_sync(
    keys: list[str] | None = None, *, check_abstention: bool = True
) -> list[CaseResult]:
    return asyncio.run(run_all(keys, check_abstention=check_abstention))
