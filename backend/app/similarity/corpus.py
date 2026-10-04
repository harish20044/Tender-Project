"""Building the corpus of past tenders to compare against.

Seeded from tenders already scraped from the portal rather than from
generated data. The comparison is only worth making if the things being
compared are real, and ``is_synthetic`` records which rows are which so a
claim made about this corpus stays honest if augmented rows are ever added.

A scraped listing carries a title, an authority, a category and dates — but
not an awarded value or a bidder count, which only appear after award. Those
columns stay null rather than being invented, and the comparison works with
what is actually there.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select

from app.core.logging import get_logger
from app.db import models
from app.db.session import session_scope
from app.providers.base import EmbeddingProvider

logger = get_logger(__name__)

# MinHash answers a different question from embedding similarity: not "is this
# like that" but "is this the same tender reissued". 128 permutations keeps the
# signature small enough to store as JSON while holding the error near 7%.
MINHASH_PERMUTATIONS = 128


@dataclass
class SeedResult:
    created: int
    updated: int
    embedded: int
    total: int


def shingles(text: str, size: int = 4) -> set[str]:
    """Word-level shingles, which is what the MinHash signature is built from.

    Word shingles rather than characters: tender titles share long boilerplate
    runs ("construction of", "including all civil works"), and character
    shingles make almost any two of them look alike.
    """
    words = [word for word in text.lower().split() if word]
    if len(words) < size:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + size]) for i in range(len(words) - size + 1)}


def minhash_signature(text: str) -> list[int]:
    from datasketch import MinHash

    minhash = MinHash(num_perm=MINHASH_PERMUTATIONS)
    for shingle in shingles(text):
        minhash.update(shingle.encode("utf-8"))
    return [int(value) for value in minhash.hashvalues]


def _scope_text(title: str, category: str | None, authority: str | None) -> str:
    """What gets embedded: the parts that describe the work itself."""
    return " ".join(part for part in (title, category, authority) if part)


async def seed_from_scraped(
    *, embedder: EmbeddingProvider, limit: int | None = None, only_construction: bool = True
) -> SeedResult:
    """Copy scraped tenders into the historical corpus, embedded and hashed."""
    with session_scope() as session:
        statement = select(models.Tender)
        if only_construction:
            statement = statement.where(models.Tender.is_construction.is_(True))
        if limit:
            statement = statement.limit(limit)
        sources = [
            {
                "reference": row.reference_number,
                "title": row.title,
                "authority": row.issuing_authority,
                "category": row.work_category,
                "published": row.published_date,
                "value": float(row.estimated_value) if row.estimated_value else None,
            }
            for row in session.scalars(statement).all()
        ]

    if not sources:
        return SeedResult(0, 0, 0, 0)

    texts = [_scope_text(s["title"], s["category"], s["authority"]) for s in sources]  # type: ignore[arg-type]
    vectors = await embedder.embed(texts)

    created = updated = 0
    with session_scope() as session:
        for source, text, vector in zip(sources, texts, vectors, strict=True):
            row = session.scalar(
                select(models.HistoricalTender).where(
                    models.HistoricalTender.reference_number == source["reference"]
                )
            )
            if row is None:
                row = models.HistoricalTender(reference_number=source["reference"])
                session.add(row)
                created += 1
            else:
                updated += 1

            row.title = source["title"]  # type: ignore[assignment]
            row.issuing_authority = source["authority"]  # type: ignore[assignment]
            row.work_category = source["category"]  # type: ignore[assignment]
            row.published_date = source["published"]  # type: ignore[assignment]
            row.estimated_value = source["value"]  # type: ignore[assignment]
            row.scope_embedding = list(vector)
            row.minhash_signature = minhash_signature(text)
            # These came off the live portal, not a generator.
            row.is_synthetic = False
            row.source = "eprocure.gov.in/cppp"

        # The session is created with autoflush off, so the rows just added
        # are invisible to a count until they are pushed explicitly.
        session.flush()
        total = session.scalar(select(func.count()).select_from(models.HistoricalTender))

    logger.info("corpus_seeded", created=created, updated=updated, total=total)
    return SeedResult(created=created, updated=updated, embedded=len(vectors), total=total or 0)
