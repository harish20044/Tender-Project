"""Finding the passages that answer a question.

Hybrid on purpose. Tender questions are full of exact terms — clause numbers,
"earnest money deposit", statutory names, an IS code — that dense vectors
blur together, and equally full of paraphrase ("how much do we have to put
down up front?") that keyword search cannot reach at all. Running both and
fusing the rankings covers what either alone misses.

Fusion is reciprocal rank rather than score addition, because the two scores
are not on comparable scales: cosine distance and ts_rank cannot be added
without inventing a weighting that happens to suit whichever query it was
tuned on.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db import models
from app.db.session import session_scope
from app.pipeline.chunk import offset_of
from app.providers.base import EmbeddingProvider, RerankProvider

logger = get_logger(__name__)

# Rank-fusion constant. 60 is the value the original reciprocal-rank-fusion
# work settled on; it damps the difference between the top few ranks so one
# retriever's confident first place cannot alone decide the result.
_RRF_K = 60


@dataclass
class Passage:
    chunk_id: str
    content: str
    page_from: int
    page_to: int
    document_id: str
    filename: str
    score: float
    # [[page, offset], ...] inside `content`; see models.Chunk.page_offsets.
    page_offsets: list[list[int]] = field(default_factory=list)

    @property
    def citation(self) -> str:
        if self.page_from == self.page_to:
            return f"{self.filename} p.{self.page_from}"
        return f"{self.filename} pp.{self.page_from}-{self.page_to}"

    def page_at(self, offset: int) -> int:
        page = self.page_from
        for entry in self.page_offsets:
            if len(entry) == 2 and offset >= entry[1]:
                page = entry[0]
            else:
                break
        return page

    def page_of(self, quote: str) -> int | None:
        """The page a quote sits on, or None when it is not in this passage.

        This is what turns a range like "pp. 3-4" into the page an estimator
        can actually turn to.
        """
        if not quote:
            return None
        offset = offset_of(quote, self.content)
        return self.page_at(offset) if offset >= 0 else None

    def cite_for(self, quote: str | None) -> str:
        """A citation narrowed to one page where the quote allows it."""
        page = self.page_of(quote) if quote else None
        return f"{self.filename} p.{page}" if page else self.citation


def _rows_to_passages(session: Session, chunk_ids: list[str]) -> dict[str, Passage]:
    if not chunk_ids:
        return {}
    rows = session.execute(
        select(
            models.Chunk.id,
            models.Chunk.content,
            models.Chunk.page_from,
            models.Chunk.page_to,
            models.Chunk.page_offsets,
            models.Document.id,
            models.Document.filename,
        )
        .join(models.DocumentVersion, models.Chunk.document_version_id == models.DocumentVersion.id)
        .join(models.Document, models.DocumentVersion.document_id == models.Document.id)
        .where(models.Chunk.id.in_(chunk_ids))
    ).all()
    return {
        str(row[0]): Passage(
            chunk_id=str(row[0]),
            content=row[1],
            page_from=row[2],
            page_to=row[3],
            page_offsets=[list(entry) for entry in (row[4] or [])],
            document_id=str(row[5]),
            filename=row[6],
            score=0.0,
        )
        for row in rows
    }


def _vector_ranking(session: Session, vector: list[float], tender_id: str, limit: int) -> list[str]:
    statement = (
        select(models.Chunk.id)
        .join(models.DocumentVersion, models.Chunk.document_version_id == models.DocumentVersion.id)
        .join(models.Document, models.DocumentVersion.document_id == models.Document.id)
        .where(models.Document.tender_id == tender_id, models.Chunk.embedding.is_not(None))
        .order_by(models.Chunk.embedding.cosine_distance(vector))
        .limit(limit)
    )
    return [str(row[0]) for row in session.execute(statement).all()]


def _keyword_ranking(session: Session, query: str, tender_id: str, limit: int) -> list[str]:
    # plainto_tsquery rather than to_tsquery: the input is a user's sentence,
    # not tsquery syntax, and to_tsquery raises on ordinary punctuation.
    statement = (
        select(models.Chunk.id)
        .join(models.DocumentVersion, models.Chunk.document_version_id == models.DocumentVersion.id)
        .join(models.Document, models.DocumentVersion.document_id == models.Document.id)
        .where(
            models.Document.tender_id == tender_id,
            text("to_tsvector('english', chunks.content) @@ plainto_tsquery('english', :q)"),
        )
        .order_by(
            func.ts_rank(
                func.to_tsvector("english", models.Chunk.content),
                func.plainto_tsquery("english", query),
            ).desc()
        )
        .limit(limit)
        .params(q=query)
    )
    return [str(row[0]) for row in session.execute(statement).all()]


async def retrieve(
    question: str,
    *,
    tender_id: str,
    embedder: EmbeddingProvider,
    reranker: RerankProvider | None = None,
    limit: int = 8,
    candidates: int = 30,
) -> list[Passage]:
    """Passages most likely to answer ``question``, best first."""
    # is_query matters: passages and questions occupy the same space but are
    # encoded differently, and embedding a question as though it were a
    # passage measurably degrades what comes back.
    vectors = await embedder.embed([question], is_query=True)
    vector = list(vectors[0])

    with session_scope() as session:
        dense = _vector_ranking(session, vector, tender_id, candidates)
        lexical = _keyword_ranking(session, question, tender_id, candidates)

        fused: dict[str, float] = {}
        for ranking in (dense, lexical):
            for position, chunk_id in enumerate(ranking):
                fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (_RRF_K + position + 1)

        ordered = sorted(fused, key=lambda cid: fused[cid], reverse=True)[:candidates]
        passages = _rows_to_passages(session, ordered)

    results = [passages[cid] for cid in ordered if cid in passages]
    for passage in results:
        passage.score = fused[passage.chunk_id]

    logger.info(
        "retrieved",
        question=question[:80],
        dense=len(dense),
        lexical=len(lexical),
        fused=len(results),
    )

    if reranker is not None and results:
        ranked = await reranker.rerank(question, [p.content for p in results], top_n=limit)
        reordered = []
        for item in ranked:
            passage = results[item.index]
            passage.score = item.score
            reordered.append(passage)
        return reordered

    return results[:limit]
