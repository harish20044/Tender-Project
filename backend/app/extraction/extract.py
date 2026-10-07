"""Reading structured facts out of a tender, with provenance.

The model's job here is narrow on purpose: find the figure and say which page
it came from. It is not asked to judge, convert currencies, or compute
anything — the decision engine does that in ordinary code, where the
arithmetic is inspectable and repeatable.

Every extracted fact carries the page it was read from and the sentence it was
read out of. A fact with no page is treated as unverified by the decision
engine rather than being quietly trusted, so provenance is not decoration.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.core.logging import get_logger
from app.db import models
from app.db.session import session_scope
from app.extraction.schema import FACTS_BY_KEY, GROUPS, FactSpec, facts_in, json_schema_for
from app.pipeline.retrieve import Passage, retrieve
from app.providers.base import ChatMessage, ChatProvider, EmbeddingProvider

logger = get_logger(__name__)

_SYSTEM = """You read figures out of construction tender documents.

For each field, find the value in the extracts and report it with the page it \
came from and the exact sentence containing it.

Rules:
- Report only what the extracts state. Never infer, estimate or calculate.
- If a field is not stated in the extracts, set its value to null. Do not guess.
- Money is a plain number of rupees: "Rs. 1,42,25,000" becomes 14225000. \
"Rs. 2.5 crore" becomes 25000000. "Rs. 50 lakh" becomes 5000000.
- Percentages are plain numbers: "5%" becomes 5.
- Durations are in days: "24 months" becomes 730, "60 months" becomes 1825.
- Dates are ISO 8601: "21 October 2026" becomes "2026-10-21".
- confidence is 0 to 1: how certain you are the value is what the field asks \
for. Use 0 when the value is null."""


@dataclass
class ExtractedFact:
    key: str
    value: object
    unit: str | None
    confidence: float
    page: int | None
    quote: str | None
    source_chunk_id: str | None
    # [x0, y0, x1, y1] in PDF points: the region of `page` the quote was
    # printed in, so the interface can highlight it rather than only name the
    # page. None when the quote could not be located in a passage.
    bbox: list[float] | None = None


def _group_query(group: str) -> str:
    """One retrieval query covering a group's facts."""
    return " ".join(fact.prompt for fact in facts_in(group))


def _field_lines(group: str) -> str:
    lines = []
    for fact in facts_in(group):
        unit = f" (in {fact.unit})" if fact.unit else ""
        lines.append(f"- {fact.key.replace('.', '__')}: {fact.prompt}{unit}")
    return "\n".join(lines)


def _passage_block(passages: list[Passage]) -> str:
    parts = []
    for passage in passages:
        parts.append(f"[page {passage.page_from}-{passage.page_to}]\n{passage.content}")
    return "\n\n".join(parts)


def _page_for(
    quote: str | None, passages: list[Passage]
) -> tuple[int | None, str | None, list[float] | None]:
    """Find which passage a quote actually came from, and where on the page.

    The model is asked for a page number, but it reads those off the headers
    in the prompt and sometimes transposes them. Locating the quote in the
    passages is authoritative where it succeeds, and is what makes a citation
    checkable rather than merely plausible.
    """
    if not quote:
        return None, None, None
    for passage in passages:
        page = passage.page_of(quote)
        if page is not None:
            # The page the quote sits on, not the first page of the passage's
            # range — a passage carrying a page break would otherwise cite the
            # wrong page for everything after it.
            return page, passage.chunk_id, passage.bbox_of(quote)
    return None, None, None


async def extract_group(
    group: str,
    *,
    tender_id: str,
    chat: ChatProvider,
    embedder: EmbeddingProvider,
) -> list[ExtractedFact]:
    """Extract one group's facts from the tender's own documents."""
    passages = await retrieve(_group_query(group), tender_id=tender_id, embedder=embedder, limit=10)
    if not passages:
        return []

    messages = [
        ChatMessage(role="system", content=_SYSTEM),
        ChatMessage(
            role="user",
            content=(
                f"Fields to find:\n{_field_lines(group)}\n\nExtracts:\n\n{_passage_block(passages)}"
            ),
        ),
    ]
    completion = await chat.complete(messages, json_schema=json_schema_for(group), max_tokens=3000)

    try:
        payload = json.loads(completion.text or "{}")
    except json.JSONDecodeError:
        logger.error("extraction_unparsable", group=group, raw=(completion.text or "")[:200])
        return []

    results: list[ExtractedFact] = []
    for fact in facts_in(group):
        entry = payload.get(fact.key.replace(".", "__")) or {}
        value = entry.get("value")
        if value is None:
            continue

        quote = entry.get("quote")
        page, chunk_id, bbox = _page_for(quote, passages)
        if page is None:
            # Fall back to the model's own page claim, but only as a claim.
            reported = entry.get("page")
            page = int(reported) if isinstance(reported, int | float) else None

        results.append(
            ExtractedFact(
                key=fact.key,
                value=value,
                unit=fact.unit,
                confidence=float(entry.get("confidence") or 0.0),
                page=page,
                quote=quote,
                source_chunk_id=chunk_id,
                bbox=bbox,
            )
        )

    logger.info("group_extracted", group=group, found=len(results), of=len(facts_in(group)))
    return results


async def extract_tender(
    tender_id: str, *, chat: ChatProvider, embedder: EmbeddingProvider
) -> list[ExtractedFact]:
    """Extract every known fact and persist it against the tender."""
    with session_scope() as session:
        version_id = session.scalar(
            select(models.DocumentVersion.id)
            .join(models.Document, models.DocumentVersion.document_id == models.Document.id)
            .where(models.Document.tender_id == tender_id)
            .order_by(models.DocumentVersion.uploaded_at.desc())
        )
    if version_id is None:
        logger.warning("extraction_without_documents", tender_id=tender_id)
        return []

    facts: list[ExtractedFact] = []
    for group in GROUPS:
        facts.extend(await extract_group(group, tender_id=tender_id, chat=chat, embedder=embedder))

    with session_scope() as session:
        for fact in facts:
            spec: FactSpec = FACTS_BY_KEY[fact.key]
            row = session.scalar(
                select(models.ExtractedFact).where(
                    models.ExtractedFact.document_version_id == version_id,
                    models.ExtractedFact.key == fact.key,
                )
            )
            if row is None:
                row = models.ExtractedFact(
                    tender_id=tender_id,
                    document_version_id=version_id,
                    key=fact.key,
                )
                session.add(row)
            # JSONB, so the value is wrapped rather than stored bare: a top
            # level scalar is legal JSON but awkward to query alongside the
            # richer values later facts will carry.
            row.value = {"value": fact.value}
            row.unit = spec.unit
            row.confidence = max(0.0, min(1.0, fact.confidence))
            row.page = fact.page
            row.quote = fact.quote
            row.bbox = fact.bbox
            row.source_chunk_id = uuid.UUID(fact.source_chunk_id) if fact.source_chunk_id else None

        session.get(models.Tender, tender_id).ingest_status = models.IngestStatus.COMPLETE  # type: ignore[union-attr]

    logger.info("tender_extracted", tender_id=tender_id, facts=len(facts))
    return facts


def stored_facts(tender_id: str) -> dict[str, dict[str, Any]]:
    """Every extracted fact for a tender, keyed for the decision engine."""
    with session_scope() as session:
        rows = session.scalars(
            select(models.ExtractedFact).where(models.ExtractedFact.tender_id == tender_id)
        ).all()
        return {
            row.key: {
                "value": (row.corrected_value or row.value or {}).get("value"),
                "unit": row.unit,
                "confidence": row.confidence,
                "page": row.page,
                "bbox": row.bbox,
                "quote": row.quote,
                "corrected": row.corrected_value is not None,
            }
            for row in rows
        }
