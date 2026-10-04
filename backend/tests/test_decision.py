"""Tests for the decision engine.

The engine is the part of this system that must never surprise anyone: it
decides whether to commit a company to a bid, and it has to be defensible
afterwards. So these tests pin the behaviour that matters — a failed mandatory
gate overrides everything, a missing fact never counts as satisfied, and the
same inputs always produce the same output.

No network and no database: the engine takes facts and a profile and returns a
decision, which is exactly what makes it testable.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import GateOutcome, Recommendation
from app.decision.engine import RULESET_VERSION, decide
from app.decision.profile import DEFAULT_PROFILE


def _future(days: int) -> str:
    return (datetime.now(UTC).date() + timedelta(days=days)).isoformat()


def _facts(**overrides: object) -> dict[str, dict[str, object]]:
    """A tender this company comfortably qualifies for, before overrides."""
    base: dict[str, object] = {
        "value.estimated": 500_000_000,
        "emd.amount": 5_000_000,
        "eligibility.annual_turnover_minimum": 400_000_000,
        "eligibility.net_worth_minimum": 50_000_000,
        "eligibility.similar_work_value_minimum": 300_000_000,
        "eligibility.experience_years_minimum": 5,
        "date.bid_submission": _future(45),
        "duration.completion_days": 540,
        "risk.liquidated_damages_percent_per_day": 0.05,
    }
    base.update(overrides)
    return {
        key: {"value": value, "unit": None, "confidence": 1.0, "page": 1, "quote": None}
        for key, value in base.items()
        if value is not None
    }


def _gate(decision: object, key: str) -> object:
    return next(g for g in decision.gates if g.key == key)  # type: ignore[attr-defined]


# --- the happy path -------------------------------------------------------- #


def test_a_tender_the_company_qualifies_for_is_a_bid() -> None:
    decision = decide(_facts(), DEFAULT_PROFILE)

    assert decision.recommendation is Recommendation.BID
    assert decision.deciding_gate is None
    assert decision.score > 60
    assert not decision.failed


# --- mandatory failures override everything -------------------------------- #


@pytest.mark.parametrize(
    ("fact_key", "value", "gate_key"),
    [
        ("eligibility.annual_turnover_minimum", 9_000_000_000, "turnover"),
        ("eligibility.net_worth_minimum", 9_000_000_000, "net_worth"),
        ("eligibility.similar_work_value_minimum", 9_000_000_000, "similar_work"),
        ("eligibility.experience_years_minimum", 40, "experience_years"),
        ("emd.amount", 900_000_000, "emd"),
    ],
)
def test_failing_any_mandatory_gate_forces_no_bid(
    fact_key: str, value: object, gate_key: str
) -> None:
    decision = decide(_facts(**{fact_key: value}), DEFAULT_PROFILE)

    assert decision.recommendation is Recommendation.NO_BID
    assert decision.deciding_gate == gate_key
    assert _gate(decision, gate_key).outcome is GateOutcome.FAIL  # type: ignore[attr-defined]


def test_a_passed_deadline_is_a_no_bid_however_good_the_tender() -> None:
    decision = decide(_facts(**{"date.bid_submission": _future(2)}), DEFAULT_PROFILE)

    assert decision.recommendation is Recommendation.NO_BID
    assert decision.deciding_gate == "submission_window"


def test_a_mandatory_failure_outranks_a_high_score() -> None:
    # Everything else passes, so the score stays high; the verdict must not.
    decision = decide(_facts(**{"emd.amount": 900_000_000}), DEFAULT_PROFILE)

    assert decision.score > 50
    assert decision.recommendation is Recommendation.NO_BID


# --- unknown is not a pass ------------------------------------------------- #


def test_a_missing_mandatory_fact_blocks_a_bid_rather_than_passing() -> None:
    decision = decide(_facts(**{"eligibility.annual_turnover_minimum": None}), DEFAULT_PROFILE)

    assert decision.recommendation is Recommendation.REVIEW
    assert _gate(decision, "turnover").outcome is GateOutcome.UNKNOWN  # type: ignore[attr-defined]
    assert decision.recommendation is not Recommendation.BID


def test_a_tender_with_no_facts_at_all_is_never_a_bid() -> None:
    decision = decide({}, DEFAULT_PROFILE)

    assert decision.recommendation is Recommendation.REVIEW
    assert all(g.outcome is GateOutcome.UNKNOWN for g in decision.gates)
    assert decision.score == 0.0


def test_an_unreadable_deadline_is_unknown_rather_than_assumed_fine() -> None:
    decision = decide(_facts(**{"date.bid_submission": "whenever"}), DEFAULT_PROFILE)

    assert _gate(decision, "submission_window").outcome is GateOutcome.UNKNOWN  # type: ignore[attr-defined]
    assert decision.recommendation is Recommendation.REVIEW


def test_every_unknown_gate_raises_a_missing_information_risk() -> None:
    decision = decide(_facts(**{"eligibility.net_worth_minimum": None}), DEFAULT_PROFILE)

    categories = [risk.category for risk in decision.risks]
    assert "missing_information" in categories


# --- scoring and counterfactuals ------------------------------------------- #


def test_the_score_falls_when_gates_fail() -> None:
    good = decide(_facts(), DEFAULT_PROFILE)
    bad = decide(_facts(**{"eligibility.net_worth_minimum": 9_000_000_000}), DEFAULT_PROFILE)

    assert bad.score < good.score


def test_a_failed_numeric_gate_reports_how_far_short_it_fell() -> None:
    decision = decide(_facts(**{"emd.amount": 25_000_000}), DEFAULT_PROFILE)

    shortfalls = {c["gate"]: c["shortfall"] for c in decision.counterfactuals}
    assert shortfalls["emd"] == pytest.approx(25_000_000 - DEFAULT_PROFILE.emd_ceiling)


# --- reproducibility ------------------------------------------------------- #


def test_the_same_tender_decides_identically_every_time() -> None:
    facts = _facts()

    first = decide(facts, DEFAULT_PROFILE)
    second = decide(facts, DEFAULT_PROFILE)

    assert first.recommendation is second.recommendation
    assert first.score == second.score
    assert [g.as_dict for g in first.gates] == [g.as_dict for g in second.gates]


def test_the_decision_records_which_ruleset_produced_it() -> None:
    assert decide(_facts(), DEFAULT_PROFILE).ruleset_version == RULESET_VERSION


def test_the_verdict_depends_on_the_company_not_only_the_tender() -> None:
    tender = _facts(**{"eligibility.annual_turnover_minimum": 1_000_000_000})
    smaller = replace(DEFAULT_PROFILE, annual_turnover=500_000_000)

    assert decide(tender, DEFAULT_PROFILE).recommendation is Recommendation.BID
    assert decide(tender, smaller).recommendation is Recommendation.NO_BID


# --- risk register --------------------------------------------------------- #


def test_absent_price_escalation_is_flagged_as_a_high_severity_risk() -> None:
    decision = decide(_facts(**{"risk.price_escalation_allowed": False}), DEFAULT_PROFILE)

    escalation = [r for r in decision.risks if r.category == "price_escalation_absent"]
    assert escalation and escalation[0].severity.value == "high"
