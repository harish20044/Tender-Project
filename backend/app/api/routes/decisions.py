"""Extract a tender's facts and compute its Bid/No-Bid recommendation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.core.logging import get_logger
from app.db import models
from app.db.session import session_scope
from app.decision.engine import RULESET_VERSION, decide
from app.decision.narrate import narrate
from app.decision.profile import DEFAULT_PROFILE
from app.extraction.extract import extract_tender, stored_facts
from app.providers.groq import GroqChatProvider
from app.providers.jina import JinaProvider

logger = get_logger(__name__)
router = APIRouter(prefix="/api/decisions", tags=["decisions"])


class FactItem(BaseModel):
    key: str
    value: Any = None
    unit: str | None = None
    confidence: float = 0.0
    page: int | None = None
    quote: str | None = None


class ExtractResponse(BaseModel):
    tender_id: str
    extracted: int
    facts: list[FactItem]


class GateItem(BaseModel):
    key: str
    label: str
    outcome: str
    mandatory: bool
    detail: str
    tender_value: Any = None
    threshold: Any = None
    fact_key: str | None = None
    page: int | None = None


class RiskItem(BaseModel):
    category: str
    severity: str
    summary: str
    detail: str | None = None
    page: int | None = None


class DecisionResponse(BaseModel):
    tender_id: str
    recommendation: str
    score: float
    deciding_gate: str | None
    rationale: str | None
    ruleset_version: str
    profile: str
    gates: list[GateItem]
    risks: list[RiskItem]
    counterfactuals: list[dict[str, Any]]


@router.post("/{tender_id}/extract", response_model=ExtractResponse)
async def extract(tender_id: str) -> ExtractResponse:
    """Read the decision-relevant figures out of the tender's documents."""
    facts = await extract_tender(tender_id, chat=GroqChatProvider(), embedder=JinaProvider())
    if not facts:
        raise HTTPException(
            status_code=404,
            detail="No facts could be extracted. Has a document been uploaded for this tender?",
        )
    return ExtractResponse(
        tender_id=tender_id,
        extracted=len(facts),
        facts=[
            FactItem(
                key=f.key,
                value=f.value,
                unit=f.unit,
                confidence=f.confidence,
                page=f.page,
                quote=f.quote,
            )
            for f in facts
        ],
    )


@router.get("/{tender_id}/facts", response_model=ExtractResponse)
def get_facts(tender_id: str) -> ExtractResponse:
    """Facts already extracted, without re-running extraction."""
    facts = stored_facts(tender_id)
    return ExtractResponse(
        tender_id=tender_id,
        extracted=len(facts),
        facts=[
            FactItem(
                key=key,
                value=entry.get("value"),
                unit=entry.get("unit"),
                confidence=entry.get("confidence") or 0.0,
                page=entry.get("page"),
                quote=entry.get("quote"),
            )
            for key, entry in facts.items()
        ],
    )


class CorrectionRequest(BaseModel):
    value: Any
    corrected_by: str | None = None


@router.patch("/{tender_id}/facts/{fact_key:path}", response_model=FactItem)
def correct_fact(
    tender_id: str,
    fact_key: str,
    correction: Annotated[CorrectionRequest, Body()],
) -> FactItem:
    """Override an extracted value by hand.

    The original is kept rather than overwritten: a correction is the only
    honest signal this system gets about how well extraction is doing, and
    losing what the model actually said would throw that away. Everything
    downstream reads the corrected value in preference, so a fixed figure
    changes the decision the next time it is scored.
    """
    with session_scope() as session:
        row = session.scalar(
            select(models.ExtractedFact).where(
                models.ExtractedFact.tender_id == tender_id,
                models.ExtractedFact.key == fact_key,
            )
        )
        if row is None:
            raise HTTPException(status_code=404, detail=f"No extracted fact {fact_key!r}.")

        row.corrected_value = {"value": correction.value}
        row.corrected_by = correction.corrected_by or "analyst"
        row.corrected_at = datetime.now(UTC)

        logger.info(
            "fact_corrected",
            tender_id=tender_id,
            key=fact_key,
            was=(row.value or {}).get("value"),
            now=correction.value,
        )
        return FactItem(
            key=row.key,
            value=correction.value,
            unit=row.unit,
            confidence=row.confidence,
            page=row.page,
            quote=row.quote,
        )


@router.post("/{tender_id}", response_model=DecisionResponse)
async def compute_decision(
    tender_id: str,
    explain: Annotated[bool, Query(description="Have the model narrate the scorecard")] = True,
) -> DecisionResponse:
    """Compute, persist and return the Bid/No-Bid recommendation.

    The recommendation itself is computed in ordinary code from the stored
    facts; the model is only asked to put the resulting scorecard into prose,
    and is given no opportunity to change the verdict.
    """
    facts = stored_facts(tender_id)
    if not facts:
        raise HTTPException(
            status_code=409,
            detail="No extracted facts for this tender. Run extraction first.",
        )

    decision = decide(facts, DEFAULT_PROFILE)
    rationale = None
    if explain:
        rationale = await narrate(decision, chat=GroqChatProvider())

    with session_scope() as session:
        version_ids = [
            str(row)
            for row in session.scalars(
                select(models.DocumentVersion.id)
                .join(
                    models.Document,
                    models.DocumentVersion.document_id == models.Document.id,
                )
                .where(models.Document.tender_id == tender_id)
            ).all()
        ]
        row = models.Decision(
            tender_id=tender_id,
            recommendation=decision.recommendation,
            score=decision.score,
            deciding_gate=decision.deciding_gate,
            rationale=rationale,
            gates=[g.as_dict for g in decision.gates],
            counterfactuals=decision.counterfactuals,
            ruleset_version=RULESET_VERSION,
            document_version_ids=version_ids,
        )
        session.add(row)
        session.flush()
        for risk in decision.risks:
            session.add(
                models.RiskFinding(
                    decision_id=row.id,
                    category=risk.category,
                    severity=risk.severity,
                    summary=risk.summary,
                    detail=risk.detail,
                    page=risk.page,
                    quote=risk.quote,
                )
            )

    return DecisionResponse(
        tender_id=tender_id,
        recommendation=str(decision.recommendation),
        score=decision.score,
        deciding_gate=decision.deciding_gate,
        rationale=rationale,
        ruleset_version=RULESET_VERSION,
        profile=DEFAULT_PROFILE.name,
        gates=[GateItem(**g.as_dict) for g in decision.gates],
        risks=[
            RiskItem(
                category=r.category,
                severity=str(r.severity),
                summary=r.summary,
                detail=r.detail,
                page=r.page,
            )
            for r in decision.risks
        ],
        counterfactuals=decision.counterfactuals,
    )


@router.get("/{tender_id}", response_model=DecisionResponse | None)
def latest_decision(tender_id: str) -> DecisionResponse | None:
    """The most recent stored decision, without recomputing."""
    with session_scope() as session:
        row = session.scalar(
            select(models.Decision)
            .where(models.Decision.tender_id == tender_id)
            .order_by(models.Decision.created_at.desc())
        )
        if row is None:
            return None
        risks = session.scalars(
            select(models.RiskFinding).where(models.RiskFinding.decision_id == row.id)
        ).all()
        return DecisionResponse(
            tender_id=tender_id,
            recommendation=str(row.recommendation),
            score=row.score,
            deciding_gate=row.deciding_gate,
            rationale=row.rationale,
            ruleset_version=row.ruleset_version,
            profile=DEFAULT_PROFILE.name,
            gates=[GateItem(**g) for g in row.gates],
            risks=[
                RiskItem(
                    category=r.category,
                    severity=str(r.severity),
                    summary=r.summary,
                    detail=r.detail,
                    page=r.page,
                )
                for r in risks
            ],
            counterfactuals=row.counterfactuals,
        )
