"""Database schema.

Two shapes recur and are worth stating up front.

**Provenance.** Anything the system asserts about a tender carries the page and
bounding box it was read from. The recommendation is only defensible if every
number in it can be traced to a region of the source PDF, so provenance is a
column on the fact, not an optional annotation.

**Versioning.** Tenders are amended by corrigendum, often repeatedly, and the
amendments tend to change exactly the fields a bid decision turns on. A
document is therefore an identity with an ordered series of versions, and a
decision records which version it was computed from.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# Must match JINA_EMBED_DIMENSIONS. Changing it requires a migration and a
# full re-embed of the corpus.
EMBEDDING_DIMENSIONS = 512


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #


class IngestStatus(str, enum.Enum):
    PENDING = "pending"
    PARSING = "parsing"
    OCR = "ocr"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    EXTRACTING = "extracting"
    COMPLETE = "complete"
    FAILED = "failed"


class DocumentKind(str, enum.Enum):
    """The parts of a tender pack, which are parsed and weighted differently."""

    NIT = "nit"  # Notice Inviting Tender
    BOQ = "boq"  # Bill of Quantities
    CONDITIONS = "conditions"  # General and special conditions of contract
    DRAWINGS = "drawings"
    CORRIGENDUM = "corrigendum"
    OTHER = "other"


class Recommendation(str, enum.Enum):
    BID = "bid"
    NO_BID = "no_bid"
    REVIEW = "review"  # Gates passed but confidence too low to assert either way


class Severity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class GateOutcome(str, enum.Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"  # The document did not yield the value; never treated as a pass


# --------------------------------------------------------------------------- #
# Tender and documents
# --------------------------------------------------------------------------- #


class Tender(Base):
    __tablename__ = "tenders"

    id: Mapped[uuid.UUID] = _uuid_pk()

    reference_number: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    issuing_authority: Mapped[str | None] = mapped_column(String(512))

    # Indian tender values run to hundreds of crores, so precision matters more
    # than range. Stored in rupees, never floats.
    estimated_value: Mapped[float | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)

    published_date: Mapped[date | None] = mapped_column(Date)
    closing_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    work_category: Mapped[str | None] = mapped_column(String(128))
    location: Mapped[str | None] = mapped_column(String(256))

    ingest_status: Mapped[IngestStatus] = mapped_column(
        Enum(IngestStatus, name="ingest_status"),
        default=IngestStatus.PENDING,
        nullable=False,
    )
    ingest_error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    documents: Mapped[list[Document]] = relationship(
        back_populates="tender", cascade="all, delete-orphan"
    )
    decisions: Mapped[list[Decision]] = relationship(
        back_populates="tender", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("reference_number", name="uq_tenders_reference_number"),
        Index("ix_tenders_closing_date", "closing_date"),
        Index("ix_tenders_ingest_status", "ingest_status"),
    )


class Document(Base):
    """A part of the tender pack, identified across all of its versions."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tender_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )

    kind: Mapped[DocumentKind] = mapped_column(
        Enum(DocumentKind, name="document_kind"), default=DocumentKind.OTHER, nullable=False
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    tender: Mapped[Tender] = relationship(back_populates="documents")
    versions: Mapped[list[DocumentVersion]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentVersion.version"
    )

    __table_args__ = (Index("ix_documents_tender_id", "tender_id"),)


class DocumentVersion(Base):
    """One uploaded revision. Version 1 is the original issue; later versions
    are corrigenda, which the comparison view diffs against their predecessor."""

    __tablename__ = "document_versions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )

    version: Mapped[int] = mapped_column(Integer, nullable=False)

    s3_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    # Uploading the same file twice must not re-run the pipeline or re-spend
    # tokens, so the raw bytes are hashed on arrival.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)

    # True when any page lacked a text layer and was routed through OCR.
    required_ocr: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Parser output: section tree, tables and reading order, kept so the
    # pipeline can be re-run downstream of parsing without re-parsing.
    parsed_content: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    document: Mapped[Document] = relationship(back_populates="versions")
    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document_version", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_document_versions_document_version"),
        Index("ix_document_versions_content_hash", "content_hash"),
    )


# --------------------------------------------------------------------------- #
# Retrieval
# --------------------------------------------------------------------------- #


class Chunk(Base):
    """A retrievable passage, carrying enough provenance to be cited."""

    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
    )

    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Identical text embeds once across the whole corpus. Boilerplate clauses
    # repeat heavily between tenders, so this saves a large share of spend.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    page_from: Mapped[int] = mapped_column(Integer, nullable=False)
    page_to: Mapped[int] = mapped_column(Integer, nullable=False)
    # [x0, y0, x1, y1] in PDF points, for the viewer's highlight overlay.
    bbox: Mapped[list[float] | None] = mapped_column(JSONB)

    # Clause or section reference as printed in the document, which is how
    # people actually refer to tender terms.
    section_path: Mapped[str | None] = mapped_column(String(512))
    is_table: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))

    document_version: Mapped[DocumentVersion] = relationship(back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("document_version_id", "ordinal", name="uq_chunks_version_ordinal"),
        Index("ix_chunks_content_hash", "content_hash"),
        # Lexical half of hybrid retrieval. Tender queries are full of exact
        # terms — clause numbers, statutory names — that dense vectors blur.
        Index(
            "ix_chunks_content_fts",
            func.to_tsvector("english", "content"),
            postgresql_using="gin",
        ),
        # Dense half. Lists are built once per document and read constantly,
        # so an HNSW index is worth its build cost.
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #


class ExtractedFact(Base):
    """One value read out of the document, with where it came from.

    Facts are stored rather than only their derived scorecard so that a
    recomputed decision can be diffed against an earlier one, and so a human
    correction has somewhere to live.
    """

    __tablename__ = "extracted_facts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tender_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
    )

    # Dotted key from the extraction schema, for example
    # "eligibility.annual_turnover_minimum".
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    unit: Mapped[str | None] = mapped_column(String(32))

    confidence: Mapped[float] = mapped_column(Float, nullable=False)

    # Provenance. A fact without a page is not citable and is treated as
    # unverified by the decision engine.
    page: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[list[float] | None] = mapped_column(JSONB)
    source_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chunks.id", ondelete="SET NULL")
    )
    quote: Mapped[str | None] = mapped_column(Text)

    # Set when an analyst overrides the extracted value. The original is kept
    # in `value`, so corrections form an evaluation signal over time.
    corrected_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    corrected_by: Mapped[str | None] = mapped_column(String(256))
    corrected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "document_version_id", "key", name="uq_extracted_facts_version_key"
        ),
        Index("ix_extracted_facts_tender_id", "tender_id"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_extracted_facts_confidence"),
    )


class FaqAnswer(Base):
    """One of the fifty standard questions, answered once at ingest and cached.

    Recomputing these per page view would be the single largest source of
    avoidable token spend in the system.
    """

    __tablename__ = "faq_answers"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tender_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )

    faq_key: Mapped[str] = mapped_column(String(128), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)

    # False when retrieval returned nothing that supports an answer. The
    # interface says so rather than showing a fluent guess.
    is_answerable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)

    # Chunk ids backing the answer, each resolvable to a page for citation.
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("tender_id", "faq_key", name="uq_faq_answers_tender_key"),
    )


# --------------------------------------------------------------------------- #
# Decision
# --------------------------------------------------------------------------- #


class Decision(Base):
    """A computed recommendation.

    Recomputing an unchanged tender must give an identical result, so the
    inputs are recorded alongside the output: which document version was read,
    which rule set applied, and which company profile it was judged against.
    """

    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tender_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )

    recommendation: Mapped[Recommendation] = mapped_column(
        Enum(Recommendation, name="recommendation"), nullable=False
    )

    # 0-100, and advisory only. A failed mandatory gate forces NO_BID whatever
    # the score, and `deciding_gate` records which gate settled it.
    score: Mapped[float] = mapped_column(Float, nullable=False)
    deciding_gate: Mapped[str | None] = mapped_column(String(128))

    # Written by the model from the scorecard below. It introduces no fact that
    # is not already present in `gates` or the risk findings.
    rationale: Mapped[str | None] = mapped_column(Text)

    # One entry per gate: threshold, our value, outcome, and the fact id it
    # was read from.
    gates: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)

    # Where a gate failed narrowly, what would close the gap.
    counterfactuals: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, nullable=False
    )

    # Reproducibility inputs.
    ruleset_version: Mapped[str] = mapped_column(String(32), nullable=False)
    company_profile_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    document_version_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    tender: Mapped[Tender] = relationship(back_populates="decisions")
    risk_findings: Mapped[list[RiskFinding]] = relationship(
        back_populates="decision", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_decisions_tender_id", "tender_id"),
        CheckConstraint("score >= 0 AND score <= 100", name="ck_decisions_score"),
    )


class RiskFinding(Base):
    """One entry in the contract risk register, tied to the clause that raised it."""

    __tablename__ = "risk_findings"

    id: Mapped[uuid.UUID] = _uuid_pk()
    decision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("decisions.id", ondelete="CASCADE"), nullable=False
    )

    # Key from the construction risk taxonomy, for example
    # "liquidated_damages" or "price_escalation_absent".
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[Severity] = mapped_column(Enum(Severity, name="severity"), nullable=False)

    summary: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)

    page: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[list[float] | None] = mapped_column(JSONB)
    quote: Mapped[str | None] = mapped_column(Text)

    decision: Mapped[Decision] = relationship(back_populates="risk_findings")

    __table_args__ = (Index("ix_risk_findings_decision_id", "decision_id"),)


# --------------------------------------------------------------------------- #
# Historical corpus
# --------------------------------------------------------------------------- #


class HistoricalTender(Base):
    """A past tender used for comparison.

    Kept separate from `tenders` because these are reference records, often
    without a full document pack, and are populated from a corpus provider
    rather than by upload.
    """

    __tablename__ = "historical_tenders"

    id: Mapped[uuid.UUID] = _uuid_pk()

    reference_number: Mapped[str | None] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    issuing_authority: Mapped[str | None] = mapped_column(String(512))

    estimated_value: Mapped[float | None] = mapped_column(Numeric(18, 2))
    awarded_value: Mapped[float | None] = mapped_column(Numeric(18, 2))
    winning_bidder: Mapped[str | None] = mapped_column(String(512))
    bidder_count: Mapped[int | None] = mapped_column(Integer)

    work_category: Mapped[str | None] = mapped_column(String(128))
    location: Mapped[str | None] = mapped_column(String(256))
    duration_days: Mapped[int | None] = mapped_column(Integer)

    published_date: Mapped[date | None] = mapped_column(Date)
    awarded_date: Mapped[date | None] = mapped_column(Date)

    # Structured eligibility profile, compared field by field against a live
    # tender rather than through text similarity alone.
    eligibility_profile: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # Embedding of the scope-of-work text, one component of the similarity
    # ensemble alongside structured features and bill-of-quantities overlap.
    scope_embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))

    # MinHash signature, used only to spot a near-identical reissue. That is a
    # different question from resemblance and is reported separately.
    minhash_signature: Mapped[list[int] | None] = mapped_column(JSONB)

    # Distinguishes a genuine record from an augmented one, so claims made
    # about real data stay honest while the corpus is still thin.
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source: Mapped[str | None] = mapped_column(String(256))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_historical_tenders_work_category", "work_category"),
        Index("ix_historical_tenders_is_synthetic", "is_synthetic"),
        Index(
            "ix_historical_tenders_scope_embedding_hnsw",
            "scope_embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"scope_embedding": "vector_cosine_ops"},
        ),
    )
