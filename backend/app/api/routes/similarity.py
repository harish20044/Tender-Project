"""Compare a tender against the corpus of past ones."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.logging import get_logger
from app.providers.jina import JinaProvider
from app.similarity.compare import corpus_size, find_similar
from app.similarity.corpus import seed_from_scraped

logger = get_logger(__name__)
router = APIRouter(prefix="/api/similarity", tags=["similarity"])


class MatchItem(BaseModel):
    reference: str | None
    title: str
    authority: str | None
    category: str | None
    published: str | None
    estimated_value: float | None
    # Outcome fields, null unless the award has been published and scraped.
    awarded_value: float | None = None
    winning_bidder: str | None = None
    bidder_count: int | None = None
    awarded: str | None = None
    # The portal publishes no currency for an awarded figure, so the
    # interface must not present it as a checked rupee amount.
    awarded_value_is_unverified: bool = True
    scope_similarity: float
    reissue_likelihood: float
    same_authority: bool
    same_category: bool
    is_probable_reissue: bool
    why: str


class SimilarResponse(BaseModel):
    tender_id: str
    corpus_size: int
    matches: list[MatchItem]


class SeedResponse(BaseModel):
    created: int
    updated: int
    embedded: int
    total: int


@router.post("/seed", response_model=SeedResponse)
async def seed(
    construction_only: Annotated[bool, Query()] = True,
    limit: Annotated[int | None, Query(ge=1, le=2000)] = None,
) -> SeedResponse:
    """Populate the historical corpus from tenders already scraped.

    Real portal records rather than generated ones, so a claim about what the
    comparison found is a claim about real tenders.
    """
    result = await seed_from_scraped(
        embedder=JinaProvider(), limit=limit, only_construction=construction_only
    )
    return SeedResponse(
        created=result.created,
        updated=result.updated,
        embedded=result.embedded,
        total=result.total,
    )


@router.get("/{tender_id}", response_model=SimilarResponse)
async def similar(
    tender_id: str,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> SimilarResponse:
    """Past tenders resembling this one, and any that look like a reissue."""
    matches = await find_similar(tender_id, embedder=JinaProvider(), limit=limit)
    return SimilarResponse(
        tender_id=tender_id,
        corpus_size=corpus_size(),
        matches=[MatchItem(**match.__dict__) for match in matches],
    )
