"""The Bid/No-Bid decision. Ordinary code, no model involved.

The recommendation is computed here, deterministically, from extracted facts
and a company profile. A model is used elsewhere to narrate the result, and
nowhere on the path to producing it — rerunning an unchanged tender must give
a byte-identical answer, and a decision a bidder cannot interrogate is a
decision they cannot defend to their own board.

Two ideas carry most of the weight:

**A failed mandatory gate forces NO_BID regardless of score.** You cannot
offset an eligibility failure with a good margin; a bid that will be rejected
unopened is worth zero no matter how attractive the work is.

**Unknown is not a pass.** A fact the documents did not yield leaves its gate
UNKNOWN, which blocks a BID recommendation and pushes the result to REVIEW.
The common failure of systems like this is to silently treat a missing figure
as satisfied, which produces a confident recommendation resting on nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from app.core.logging import get_logger
from app.db.models import GateOutcome, Recommendation, Severity
from app.decision.profile import CompanyProfile

logger = get_logger(__name__)

# Bumped whenever a threshold or gate changes, so a stored decision records
# the rules it was made under and old decisions stay interpretable.
RULESET_VERSION = "1.0.0"


@dataclass
class Gate:
    key: str
    label: str
    outcome: GateOutcome
    mandatory: bool
    detail: str
    tender_value: Any = None
    threshold: Any = None
    fact_key: str | None = None
    page: int | None = None

    @property
    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "outcome": str(self.outcome),
            "mandatory": self.mandatory,
            "detail": self.detail,
            "tender_value": self.tender_value,
            "threshold": self.threshold,
            "fact_key": self.fact_key,
            "page": self.page,
        }


@dataclass
class Risk:
    category: str
    severity: Severity
    summary: str
    detail: str | None = None
    page: int | None = None
    quote: str | None = None


@dataclass
class Decision:
    recommendation: Recommendation
    score: float
    deciding_gate: str | None
    gates: list[Gate]
    risks: list[Risk]
    counterfactuals: list[dict[str, Any]] = field(default_factory=list)
    ruleset_version: str = RULESET_VERSION

    @property
    def failed(self) -> list[Gate]:
        return [g for g in self.gates if g.outcome is GateOutcome.FAIL]

    @property
    def unknown(self) -> list[Gate]:
        return [g for g in self.gates if g.outcome is GateOutcome.UNKNOWN]


def _value(facts: dict[str, Any], key: str) -> Any:
    entry = facts.get(key)
    return entry.get("value") if isinstance(entry, dict) else None


def _page(facts: dict[str, Any], key: str) -> int | None:
    entry = facts.get(key)
    return entry.get("page") if isinstance(entry, dict) else None


def _numeric_gate(
    *,
    key: str,
    label: str,
    facts: dict[str, Any],
    fact_key: str,
    threshold: float,
    mandatory: bool,
    comparison: str,
    unit: str = "",
) -> Gate:
    """A gate comparing one tender figure against one company figure.

    ``comparison`` is "company_at_least" when the company must meet or exceed
    the tender's requirement, and "tender_at_most" when the tender's figure
    must stay within what the company tolerates.
    """
    required = _value(facts, fact_key)
    page = _page(facts, fact_key)

    if required is None:
        return Gate(
            key=key,
            label=label,
            outcome=GateOutcome.UNKNOWN,
            mandatory=mandatory,
            detail="The documents do not state this, so it cannot be checked.",
            threshold=threshold,
            fact_key=fact_key,
        )

    required = float(required)
    if comparison == "company_at_least":
        passed = threshold >= required
        detail = f"Tender requires {required:,.0f}{unit}; we have {threshold:,.0f}{unit}."
    else:
        passed = required <= threshold
        detail = f"Tender sets {required:,.2f}{unit}; our limit is {threshold:,.2f}{unit}."

    return Gate(
        key=key,
        label=label,
        outcome=GateOutcome.PASS if passed else GateOutcome.FAIL,
        mandatory=mandatory,
        detail=detail,
        tender_value=required,
        threshold=threshold,
        fact_key=fact_key,
        page=page,
    )


def _build_gates(facts: dict[str, Any], profile: CompanyProfile) -> list[Gate]:
    gates: list[Gate] = [
        _numeric_gate(
            key="turnover",
            label="Annual turnover",
            facts=facts,
            fact_key="eligibility.annual_turnover_minimum",
            threshold=profile.annual_turnover,
            mandatory=True,
            comparison="company_at_least",
        ),
        _numeric_gate(
            key="net_worth",
            label="Net worth",
            facts=facts,
            fact_key="eligibility.net_worth_minimum",
            threshold=profile.net_worth,
            mandatory=True,
            comparison="company_at_least",
        ),
        _numeric_gate(
            key="similar_work",
            label="Similar work experience",
            facts=facts,
            fact_key="eligibility.similar_work_value_minimum",
            threshold=profile.largest_completed_work,
            mandatory=True,
            comparison="company_at_least",
        ),
        _numeric_gate(
            key="experience_years",
            label="Years of experience",
            facts=facts,
            fact_key="eligibility.experience_years_minimum",
            threshold=float(profile.years_experience),
            mandatory=True,
            comparison="company_at_least",
            unit=" years",
        ),
        _numeric_gate(
            key="emd",
            label="EMD affordability",
            facts=facts,
            fact_key="emd.amount",
            threshold=profile.emd_ceiling,
            mandatory=True,
            comparison="tender_at_most",
        ),
        _numeric_gate(
            key="ld_rate",
            label="Liquidated damages rate",
            facts=facts,
            fact_key="risk.liquidated_damages_percent_per_day",
            threshold=profile.ld_per_day_tolerance,
            mandatory=False,
            comparison="tender_at_most",
            unit="%/day",
        ),
        _numeric_gate(
            key="duration",
            label="Contract duration",
            facts=facts,
            fact_key="duration.completion_days",
            threshold=float(profile.max_duration_days),
            mandatory=False,
            comparison="tender_at_most",
            unit=" days",
        ),
    ]

    gates.append(_contract_value_gate(facts, profile))
    gates.append(_submission_window_gate(facts, profile))
    return gates


def _contract_value_gate(facts: dict[str, Any], profile: CompanyProfile) -> Gate:
    """Contract value has to sit inside a band, not merely under a ceiling.

    Work far below the company's size is rarely worth the bidding cost, and
    work far above it is a capacity risk, so this is the one gate with two
    sided bounds.
    """
    value = _value(facts, "value.estimated")
    if value is None:
        return Gate(
            key="contract_value",
            label="Contract value band",
            outcome=GateOutcome.UNKNOWN,
            mandatory=False,
            detail="The documents do not state an estimated contract value.",
            fact_key="value.estimated",
        )

    value = float(value)
    inside = profile.min_contract_value <= value <= profile.max_contract_value
    return Gate(
        key="contract_value",
        label="Contract value band",
        outcome=GateOutcome.PASS if inside else GateOutcome.FAIL,
        mandatory=False,
        detail=(
            f"Tender is {value:,.0f}; our band is "
            f"{profile.min_contract_value:,.0f} to {profile.max_contract_value:,.0f}."
        ),
        tender_value=value,
        threshold=[profile.min_contract_value, profile.max_contract_value],
        fact_key="value.estimated",
        page=_page(facts, "value.estimated"),
    )


def _submission_window_gate(facts: dict[str, Any], profile: CompanyProfile) -> Gate:
    """Whether there is still time to prepare a bid.

    Mandatory, because a deadline that has passed is the one failure no amount
    of capability compensates for.
    """
    raw = _value(facts, "date.bid_submission")
    if not raw:
        return Gate(
            key="submission_window",
            label="Time to bid",
            outcome=GateOutcome.UNKNOWN,
            mandatory=True,
            detail="The documents do not state a submission deadline.",
            fact_key="date.bid_submission",
        )

    try:
        deadline = date.fromisoformat(str(raw)[:10])
    except ValueError:
        return Gate(
            key="submission_window",
            label="Time to bid",
            outcome=GateOutcome.UNKNOWN,
            mandatory=True,
            detail=f"The stated deadline {raw!r} is not a readable date.",
            fact_key="date.bid_submission",
        )

    days_left = (deadline - datetime.now(UTC).date()).days
    enough = days_left >= profile.bid_preparation_days
    return Gate(
        key="submission_window",
        label="Time to bid",
        outcome=GateOutcome.PASS if enough else GateOutcome.FAIL,
        mandatory=True,
        detail=(
            f"{days_left} days until the {deadline.isoformat()} deadline; "
            f"we need {profile.bid_preparation_days}."
        ),
        tender_value=days_left,
        threshold=profile.bid_preparation_days,
        fact_key="date.bid_submission",
        page=_page(facts, "date.bid_submission"),
    )


def _risks(facts: dict[str, Any], gates: list[Gate]) -> list[Risk]:
    risks: list[Risk] = []

    ld_cap = _value(facts, "risk.liquidated_damages_cap_percent")
    if ld_cap is not None and float(ld_cap) >= 10:
        risks.append(
            Risk(
                category="liquidated_damages",
                severity=Severity.MEDIUM,
                summary=f"Liquidated damages are capped at {float(ld_cap):.0f}% of contract price.",
                page=_page(facts, "risk.liquidated_damages_cap_percent"),
            )
        )

    escalation = _value(facts, "risk.price_escalation_allowed")
    if escalation is False:
        risks.append(
            Risk(
                category="price_escalation_absent",
                severity=Severity.HIGH,
                summary="No price escalation is payable; input cost rises are the contractor's.",
                page=_page(facts, "risk.price_escalation_allowed"),
            )
        )

    retention = _value(facts, "risk.retention_percent")
    if retention is not None and float(retention) >= 5:
        risks.append(
            Risk(
                category="retention",
                severity=Severity.LOW,
                summary=f"{float(retention):.0f}% of each bill is retained.",
                page=_page(facts, "risk.retention_percent"),
            )
        )

    for gate in gates:
        if gate.outcome is GateOutcome.UNKNOWN:
            risks.append(
                Risk(
                    category="missing_information",
                    severity=Severity.HIGH if gate.mandatory else Severity.MEDIUM,
                    summary=f"{gate.label} could not be determined from the documents.",
                    detail=gate.detail,
                )
            )

    return risks


def _counterfactuals(gates: list[Gate]) -> list[dict[str, Any]]:
    """What would have to change for a failed gate to pass.

    Only for gates that failed on a number, and only reported as a gap — the
    useful question after a no-bid is "by how much did we miss", which is
    answerable, rather than "what should we do", which is not.
    """
    out: list[dict[str, Any]] = []
    for gate in gates:
        if gate.outcome is not GateOutcome.FAIL:
            continue
        if not isinstance(gate.tender_value, int | float):
            continue
        if not isinstance(gate.threshold, int | float):
            continue
        gap = float(gate.tender_value) - float(gate.threshold)
        out.append(
            {
                "gate": gate.key,
                "label": gate.label,
                "shortfall": abs(gap),
                "statement": (f"{gate.label} misses by {abs(gap):,.2f}."),
            }
        )
    return out


def _score(gates: list[Gate]) -> float:
    """Advisory only: how much of what was checked, passed.

    Mandatory gates count double, since failing one is categorically worse
    than failing a preference. Unknowns count as zero rather than being
    excluded, so a tender the documents barely describe scores low and is
    visibly uncertain rather than accidentally scoring well on two facts.
    """
    if not gates:
        return 0.0
    earned = 0.0
    total = 0.0
    for gate in gates:
        weight = 2.0 if gate.mandatory else 1.0
        total += weight
        if gate.outcome is GateOutcome.PASS:
            earned += weight
    return round(100.0 * earned / total, 1)


def decide(facts: dict[str, Any], profile: CompanyProfile) -> Decision:
    """Compute the recommendation. Same inputs always give the same output."""
    gates = _build_gates(facts, profile)
    score = _score(gates)
    risks = _risks(facts, gates)

    mandatory_failures = [g for g in gates if g.mandatory and g.outcome is GateOutcome.FAIL]
    mandatory_unknowns = [g for g in gates if g.mandatory and g.outcome is GateOutcome.UNKNOWN]

    if mandatory_failures:
        recommendation = Recommendation.NO_BID
        deciding = mandatory_failures[0].key
    elif mandatory_unknowns:
        # Everything checkable passed, but something mandatory could not be
        # checked. That is a question for a human, not a recommendation.
        recommendation = Recommendation.REVIEW
        deciding = mandatory_unknowns[0].key
    elif score < 60:
        recommendation = Recommendation.REVIEW
        deciding = None
    else:
        recommendation = Recommendation.BID
        deciding = None

    decision = Decision(
        recommendation=recommendation,
        score=score,
        deciding_gate=deciding,
        gates=gates,
        risks=risks,
        counterfactuals=_counterfactuals(gates),
    )
    logger.info(
        "decision_computed",
        recommendation=str(recommendation),
        score=score,
        deciding_gate=deciding,
        failed=len(decision.failed),
        unknown=len(decision.unknown),
    )
    return decision
