"""Comparing a live tender against the corpus of past ones.

Three signals, reported separately rather than blended into one number,
because they answer different questions and an estimator needs to know which
one fired:

- **Scope similarity** (embeddings): is this the same kind of work?
- **Reissue likelihood** (MinHash): is this literally the same tender,
  re-advertised? That happens often when a first round draws too few bids,
  and it is worth knowing because the earlier round's outcome is informative.
- **Structural closeness**: same authority, same category, comparable value.

Collapsing these into a single percentage would hide the distinction that
matters most — "we have done work like this" is a very different statement
from "we bid on this exact job eight months ago".
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select

from app.core.logging import get_logger
from app.db import models
from app.db.session import session_scope
from app.providers.base import EmbeddingProvider
from app.similarity.corpus import minhash_signature

logger = get_logger(__name__)

# Above this, two tenders are near enough to the same text that a reissue is
# the likeliest explanation. Set high deliberately: calling an ordinary
# resemblance a reissue is the more misleading error.
REISSUE_THRESHOLD = 0.75


@dataclass
class Match:
    reference: str | None
    title: str
    authority: str | None
    category: str | None
    published: str | None
    estimated_value: float | None
    scope_similarity: float
    reissue_likelihood: float
    same_authority: bool
    same_category: bool
    is_probable_reissue: bool
    why: str


def jaccard(left: list[int], right: list[int]) -> float:
    """Estimated Jaccard similarity between two MinHash signatures."""
    if not left or not right or len(left) != len(right):
        return 0.0
    matches = sum(1 for a, b in zip(left, right, strict=True) if a == b)
    return matches / len(left)


def _explain(scope: float, reissue: float, same_authority: bool, same_category: bool) -> str:
    if reissue >= REISSUE_THRESHOLD:
        return "Near-identical wording — likely the same tender re-advertised."
    parts = []
    if scope >= 0.8:
        parts.append("very similar scope")
    elif scope >= 0.6:
        parts.append("similar scope")
    else:
        parts.append("loosely related scope")
    if same_authority:
        parts.append("same authority")
    if same_category:
        parts.append("same work category")
    return ", ".join(parts).capitalize() + "."


async def find_similar(
    tender_id: str, *, embedder: EmbeddingProvider, limit: int = 10
) -> list[Match]:
    """Past tenders most like this one, most similar first."""
    with session_scope() as session:
        tender = session.get(models.Tender, tender_id)
        if tender is None:
            return []
        subject = {
            "reference": tender.reference_number,
            "text": " ".join(
                part
                for part in (tender.title, tender.work_category, tender.issuing_authority)
                if part
            ),
            "authority": tender.issuing_authority,
            "category": tender.work_category,
        }

    vectors = await embedder.embed([subject["text"]])  # type: ignore[list-item]
    vector = list(vectors[0])
    subject_signature = minhash_signature(subject["text"])  # type: ignore[arg-type]

    with session_scope() as session:
        # Over-fetch on the vector index, then re-rank with the other two
        # signals: the index orders by scope alone, and a reissue of a
        # differently-worded title can sit below the cut on that measure.
        rows = session.execute(
            select(
                models.HistoricalTender.reference_number,
                models.HistoricalTender.title,
                models.HistoricalTender.issuing_authority,
                models.HistoricalTender.work_category,
                models.HistoricalTender.published_date,
                models.HistoricalTender.estimated_value,
                models.HistoricalTender.minhash_signature,
                models.HistoricalTender.scope_embedding.cosine_distance(vector),
            )
            .where(models.HistoricalTender.scope_embedding.is_not(None))
            # A tender is not its own precedent.
            .where(models.HistoricalTender.reference_number != subject["reference"])
            .order_by(models.HistoricalTender.scope_embedding.cosine_distance(vector))
            .limit(max(limit * 4, 40))
        ).all()

    matches: list[Match] = []
    for row in rows:
        scope = 1.0 - float(row[7])
        reissue = jaccard(subject_signature, list(row[6] or []))
        same_authority = bool(row[2] and row[2] == subject["authority"])
        same_category = bool(row[3] and row[3] == subject["category"])
        matches.append(
            Match(
                reference=row[0],
                title=row[1],
                authority=row[2],
                category=row[3],
                published=row[4].isoformat() if row[4] else None,
                estimated_value=float(row[5]) if row[5] else None,
                scope_similarity=round(scope, 4),
                reissue_likelihood=round(reissue, 4),
                same_authority=same_authority,
                same_category=same_category,
                is_probable_reissue=reissue >= REISSUE_THRESHOLD,
                why=_explain(scope, reissue, same_authority, same_category),
            )
        )

    # A probable reissue outranks a merely similar tender whatever its scope
    # score, because it is the more consequential finding.
    matches.sort(key=lambda m: (m.is_probable_reissue, m.scope_similarity), reverse=True)
    logger.info(
        "similarity_computed",
        tender_id=tender_id,
        candidates=len(rows),
        reissues=sum(1 for m in matches if m.is_probable_reissue),
    )
    return matches[:limit]


def corpus_size() -> int:
    """How many past tenders are available to compare against."""
    with session_scope() as session:
        return session.scalar(select(func.count()).select_from(models.HistoricalTender)) or 0
