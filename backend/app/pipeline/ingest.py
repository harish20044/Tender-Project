"""From an uploaded PDF to searchable, citable chunks in the database.

One pass: parse, chunk, embed, store. It is written to be re-runnable — the
same file uploaded twice produces the same rows rather than a second copy,
because tender packs get re-sent in full when a single annexure changes and
re-embedding the unchanged ninety per cent is the largest avoidable cost in
the system.

Embeddings are deduplicated by content hash across the whole corpus, not just
within a document: boilerplate clauses repeat heavily between tenders from
the same authority, and each distinct passage only needs paying for once.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db import models
from app.db.session import session_scope
from app.pipeline.chunk import chunk_document
from app.pipeline.parse import parse_pdf
from app.providers.base import EmbeddingProvider
from app.storage import DocumentStorage, content_hash

logger = get_logger(__name__)


@dataclass
class IngestResult:
    tender_id: str
    document_id: str
    document_version_id: str
    filename: str
    pages: int
    chunks: int
    embedded: int
    reused_embeddings: int
    needs_ocr: bool
    storage_key: str


def _storage_key(reference: str, filename: str) -> str:
    import re

    safe_reference = re.sub(r"[^A-Za-z0-9._-]+", "_", reference).strip("._") or "tender"
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._") or "document.pdf"
    return f"uploads/{safe_reference}/{safe_name}"


def _get_or_create_tender(session: Session, reference: str, title: str) -> models.Tender:
    tender = session.scalar(
        select(models.Tender).where(models.Tender.reference_number == reference)
    )
    if tender is None:
        tender = models.Tender(reference_number=reference, title=title)
        session.add(tender)
        session.flush()
    return tender


def _existing_embeddings(session: Session, hashes: list[str]) -> dict[str, list[float]]:
    """Vectors already computed for identical text, anywhere in the corpus."""
    if not hashes:
        return {}
    rows = session.execute(
        select(models.Chunk.content_hash, models.Chunk.embedding).where(
            models.Chunk.content_hash.in_(hashes),
            models.Chunk.embedding.is_not(None),
        )
    ).all()
    return {row[0]: list(row[1]) for row in rows if row[1] is not None}


async def ingest_pdf(
    data: bytes,
    *,
    filename: str,
    reference: str,
    title: str | None = None,
    kind: models.DocumentKind = models.DocumentKind.OTHER,
    storage: DocumentStorage,
    embedder: EmbeddingProvider,
) -> IngestResult:
    """Parse, chunk, embed and store one PDF against a tender."""
    digest = content_hash(data)
    parsed = parse_pdf(data)
    chunks = chunk_document(parsed)

    key = _storage_key(reference, filename)
    await storage.put(key, data, content_type="application/pdf")

    # Embedding happens outside the session: it is a network round trip per
    # batch, and holding a database transaction open across it would pin a
    # connection for the duration for no reason.
    with session_scope() as session:
        hashes = [content_hash(chunk.content.encode("utf-8")) for chunk in chunks]
        reusable = _existing_embeddings(session, hashes)

    pending = [
        chunk for chunk, digest_ in zip(chunks, hashes, strict=True) if digest_ not in reusable
    ]
    fresh: dict[str, list[float]] = {}
    if pending:
        vectors = await embedder.embed([chunk.content for chunk in pending])
        for chunk, vector in zip(pending, vectors, strict=True):
            fresh[content_hash(chunk.content.encode("utf-8"))] = list(vector)

    with session_scope() as session:
        tender = _get_or_create_tender(session, reference, title or filename)

        document = session.scalar(
            select(models.Document).where(
                models.Document.tender_id == tender.id,
                models.Document.filename == filename,
            )
        )
        if document is None:
            document = models.Document(tender_id=tender.id, kind=kind, filename=filename)
            session.add(document)
            session.flush()

        version = session.scalar(
            select(models.DocumentVersion).where(
                models.DocumentVersion.document_id == document.id,
                models.DocumentVersion.content_hash == digest,
            )
        )
        if version is None:
            highest = (
                session.scalar(
                    select(models.DocumentVersion.version)
                    .where(models.DocumentVersion.document_id == document.id)
                    .order_by(models.DocumentVersion.version.desc())
                )
                or 0
            )
            version = models.DocumentVersion(
                document_id=document.id,
                version=highest + 1,
                s3_key=key,
                content_hash=digest,
                byte_size=len(data),
                page_count=parsed.page_count,
                required_ocr=parsed.needs_ocr,
            )
            session.add(version)
            session.flush()
        else:
            # Same bytes already ingested; its chunks are already correct.
            logger.info("document_version_already_ingested", filename=filename)
            return IngestResult(
                tender_id=str(tender.id),
                document_id=str(document.id),
                document_version_id=str(version.id),
                filename=filename,
                pages=parsed.page_count,
                chunks=0,
                embedded=0,
                reused_embeddings=0,
                needs_ocr=parsed.needs_ocr,
                storage_key=key,
            )

        for chunk, chunk_digest in zip(chunks, hashes, strict=True):
            session.add(
                models.Chunk(
                    document_version_id=version.id,
                    ordinal=chunk.ordinal,
                    content=chunk.content,
                    content_hash=chunk_digest,
                    page_from=chunk.page_from,
                    page_to=chunk.page_to,
                    bbox=chunk.bbox,
                    section_path=chunk.section_path,
                    is_table=chunk.is_table,
                    embedding=reusable.get(chunk_digest) or fresh.get(chunk_digest),
                )
            )

        tender.ingest_status = models.IngestStatus.COMPLETE
        session.flush()

        result = IngestResult(
            tender_id=str(tender.id),
            document_id=str(document.id),
            document_version_id=str(version.id),
            filename=filename,
            pages=parsed.page_count,
            chunks=len(chunks),
            embedded=len(fresh),
            reused_embeddings=len(chunks) - len(fresh),
            needs_ocr=parsed.needs_ocr,
            storage_key=key,
        )

    logger.info(
        "pdf_ingested",
        filename=filename,
        reference=reference,
        pages=result.pages,
        chunks=result.chunks,
        embedded=result.embedded,
        reused=result.reused_embeddings,
    )
    return result
