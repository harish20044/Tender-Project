"""Upload a tender PDF and ask questions about it."""

from __future__ import annotations

import time
import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.auth import CurrentUser, Principal, record_activity, record_question, require
from app.core.logging import get_logger
from app.db import models
from app.db.session import session_scope
from app.pipeline.answer import Answer, answer_question
from app.pipeline.ingest import ingest_pdf
from app.pipeline.retrieve import retrieve
from app.providers.groq import GroqChatProvider
from app.providers.jina import JinaProvider
from app.schemas.faqs import FAQS, FAQS_BY_KEY
from app.storage import StorageError, get_storage

logger = get_logger(__name__)
router = APIRouter(prefix="/api/documents", tags=["documents"])


class UploadResponse(BaseModel):
    tender_id: str
    document_id: str
    document_version_id: str
    filename: str
    reference: str
    pages: int
    chunks: int
    embedded: int
    reused_embeddings: int
    needs_ocr: bool


class Citation(BaseModel):
    marker: int
    chunk_id: str
    filename: str
    page_from: int
    page_to: int
    citation: str
    # The region of the page the quoted sentence was printed in, where the
    # quote could be located. Points, then the same box as fractions of the
    # page — the latter is what a viewer can actually draw.
    bbox: list[float] | None = None
    bbox_relative: list[float] | None = None


class AnswerResponse(BaseModel):
    question: str
    answer: str
    is_answerable: bool
    confidence: float
    citations: list[Citation]
    elapsed_ms: int


class FaqAnswerItem(BaseModel):
    key: str
    question: str
    category: str
    answer: str
    is_answerable: bool
    confidence: float
    citations: list[Citation]


class FaqRunResponse(BaseModel):
    tender_id: str
    answered: int
    unanswerable: int
    items: list[FaqAnswerItem]


@router.post("/upload", response_model=UploadResponse)
async def upload_document(
    request: Request,
    file: Annotated[UploadFile, File(description="The tender PDF")],
    user: Principal = require(models.UserRole.ESTIMATOR, models.UserRole.MANAGER),
    reference: Annotated[str | None, Form()] = None,
    title: Annotated[str | None, Form()] = None,
) -> UploadResponse:
    """Parse, chunk, embed and store a tender PDF so it can be questioned."""
    filename = file.filename or "document.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="Only PDF uploads are supported.")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    # A reference is what everything else hangs off, so an upload without one
    # gets a generated placeholder rather than being rejected — the analyst
    # can be looking at a pack before they know its number.
    tender_reference = reference or f"UPLOAD-{uuid.uuid4().hex[:10].upper()}"

    try:
        result = await ingest_pdf(
            data,
            filename=filename,
            reference=tender_reference,
            title=title,
            storage=get_storage(),
            embedder=JinaProvider(),
        )
    except Exception as exc:
        logger.error("upload_failed", filename=filename, error=str(exc))
        raise HTTPException(status_code=500, detail=f"Could not ingest the PDF: {exc}") from exc

    record_activity(
        user,
        models.ActivityAction.VIEW_TENDER,
        tender_id=result.tender_id,
        request=request,
        extra={"uploaded": filename, "pages": result.pages},
    )

    return UploadResponse(
        tender_id=result.tender_id,
        document_id=result.document_id,
        document_version_id=result.document_version_id,
        filename=result.filename,
        reference=tender_reference,
        pages=result.pages,
        chunks=result.chunks,
        embedded=result.embedded,
        reused_embeddings=result.reused_embeddings,
        needs_ocr=result.needs_ocr,
    )


async def _answer(question: str, tender_id: str, limit: int) -> tuple[Answer, int]:
    started = time.monotonic()
    passages = await retrieve(question, tender_id=tender_id, embedder=JinaProvider(), limit=limit)
    answer = await answer_question(question, passages, chat=GroqChatProvider())
    return answer, int((time.monotonic() - started) * 1000)


@router.post("/{tender_id}/ask", response_model=AnswerResponse)
async def ask(
    tender_id: str,
    request: Request,
    question: Annotated[str, Query(min_length=3, description="A question about this tender")],
    user: Principal = require(
        models.UserRole.VIEWER, models.UserRole.ESTIMATOR, models.UserRole.MANAGER
    ),
    limit: Annotated[int, Query(ge=1, le=20)] = 8,
) -> AnswerResponse:
    """Answer a free-text question from the tender's own documents."""
    answer, elapsed = await _answer(question, tender_id, limit)

    # Logged so the questions people actually ask can be found later: one
    # asked repeatedly across users is the signal for promoting it into the
    # standard set.
    record_question(
        user,
        question,
        tender_id=tender_id,
        source=models.QuestionSource.FREE_SEARCH,
        answerable=answer.is_answerable,
        confidence=answer.confidence,
        response_ms=elapsed,
    )
    record_activity(user, models.ActivityAction.ASK_QUESTION, tender_id=tender_id, request=request)
    return AnswerResponse(
        question=question,
        answer=answer.text,
        is_answerable=answer.is_answerable,
        confidence=answer.confidence,
        citations=[Citation(**c) for c in answer.citations],
        elapsed_ms=elapsed,
    )


@router.post("/{tender_id}/faqs", response_model=FaqRunResponse)
async def run_faqs(
    tender_id: str,
    request: Request,
    user: Principal = require(models.UserRole.ESTIMATOR, models.UserRole.MANAGER),
    only: Annotated[str | None, Query(description="Answer one FAQ key only")] = None,
    category: Annotated[str | None, Query(description="Limit to one category")] = None,
) -> FaqRunResponse:
    """Answer the standard questions and cache them against the tender.

    Cached rather than recomputed per view: these answers do not change unless
    the documents do, and re-running fifty retrieval-plus-generation cycles on
    every page load is the single largest avoidable cost here.
    """
    selected = FAQS
    if only:
        faq = FAQS_BY_KEY.get(only)
        if faq is None:
            raise HTTPException(status_code=404, detail=f"Unknown FAQ key: {only}")
        selected = (faq,)
    elif category:
        selected = tuple(f for f in FAQS if f.category.lower() == category.lower())
        if not selected:
            raise HTTPException(status_code=404, detail=f"Unknown category: {category}")

    items: list[FaqAnswerItem] = []
    for faq in selected:
        answer, _ = await _answer(faq.question, tender_id, 8)
        items.append(
            FaqAnswerItem(
                key=faq.key,
                question=faq.question,
                category=faq.category,
                answer=answer.text,
                is_answerable=answer.is_answerable,
                confidence=answer.confidence,
                citations=[Citation(**c) for c in answer.citations],
            )
        )

        with session_scope() as session:
            existing = session.scalar(
                select(models.FaqAnswer).where(
                    models.FaqAnswer.tender_id == tender_id,
                    models.FaqAnswer.faq_key == faq.key,
                )
            )
            if existing is None:
                existing = models.FaqAnswer(tender_id=tender_id, faq_key=faq.key)
                session.add(existing)
            existing.question = faq.question
            existing.answer = items[-1].answer
            existing.is_answerable = items[-1].is_answerable
            existing.confidence = items[-1].confidence
            existing.citations = [c.model_dump() for c in items[-1].citations]

    record_activity(user, models.ActivityAction.VIEW_FAQ, tender_id=tender_id, request=request)
    answered = sum(1 for i in items if i.is_answerable)
    return FaqRunResponse(
        tender_id=tender_id,
        answered=answered,
        unanswerable=len(items) - answered,
        items=items,
    )


@router.get("/raw/{key:path}")
async def raw_document(key: str) -> Response:
    """Serve a stored document.

    The filesystem backend's `url_for` has always pointed here; until now
    nothing answered, so every link it produced was dead. Served through the
    API rather than from disk directly because the filesystem backend has no
    concept of an expiring URL, and this is the only place that can later
    grow an access check.
    """
    try:
        data = await get_storage().get(key)
    except StorageError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    media_type = "application/pdf" if key.lower().endswith(".pdf") else "application/octet-stream"
    return Response(
        content=data,
        media_type=media_type,
        headers={
            # inline, so a browser opens it at a page rather than downloading it
            "Content-Disposition": f'inline; filename="{key.rsplit("/", 1)[-1]}"'
        },
    )


class TenderDocumentSummary(BaseModel):
    tender_id: str
    reference: str
    title: str
    documents: int
    chunks: int
    pages: int
    # Where the most recent document lives, so a caller can link straight to
    # the source rather than reconstructing the key and guessing wrong.
    document_url: str | None = None


@router.get("", response_model=list[TenderDocumentSummary])
def list_uploaded(user: CurrentUser) -> list[TenderDocumentSummary]:
    """Tenders that have documents ingested and are therefore questionable."""
    with session_scope() as session:
        rows = session.execute(
            select(
                models.Tender.id,
                models.Tender.reference_number,
                models.Tender.title,
                func.count(func.distinct(models.Document.id)),
                func.count(func.distinct(models.Chunk.id)),
                func.coalesce(func.sum(func.distinct(models.DocumentVersion.page_count)), 0),
                func.max(models.DocumentVersion.s3_key),
            )
            .join(models.Document, models.Document.tender_id == models.Tender.id)
            .join(
                models.DocumentVersion,
                models.DocumentVersion.document_id == models.Document.id,
            )
            .outerjoin(
                models.Chunk,
                models.Chunk.document_version_id == models.DocumentVersion.id,
            )
            .group_by(models.Tender.id, models.Tender.reference_number, models.Tender.title)
            .order_by(models.Tender.updated_at.desc())
        ).all()

    return [
        TenderDocumentSummary(
            tender_id=str(r[0]),
            reference=r[1],
            title=r[2],
            documents=r[3],
            chunks=r[4],
            pages=int(r[5] or 0),
            document_url=f"/api/documents/raw/{r[6]}" if r[6] else None,
        )
        for r in rows
    ]
