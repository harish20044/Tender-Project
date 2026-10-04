"""The facts a bid decision turns on, and how to ask for them.

This is deliberately a short list. The decision engine can only use a fact it
can compare against a company profile or a threshold, so extracting prose
that reads well but cannot be scored is wasted spend — every key here feeds
at least one gate in ``app.decision``.

Each fact declares the unit it is expected in, which is what lets the engine
compare "Rs. 1,42,25,000" against a company's EMD ceiling without the model
being asked to do arithmetic. Models are unreliable at arithmetic and
perfectly good at reading a figure off a page; the division of labour here
follows that.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FactType(StrEnum):
    MONEY = "money"  # Indian rupees, as a plain number
    DURATION_DAYS = "duration_days"
    PERCENT = "percent"
    DATE = "date"  # ISO 8601
    INTEGER = "integer"
    TEXT = "text"
    BOOLEAN = "boolean"


@dataclass(frozen=True)
class FactSpec:
    key: str
    prompt: str
    type: FactType
    group: str
    unit: str | None = None


# Grouped so one retrieval serves several related facts: the clauses that
# state an EMD amount and the form it takes are almost always adjacent, and
# retrieving once per group rather than once per fact cuts both latency and
# token spend by roughly the group size.
FACTS: tuple[FactSpec, ...] = (
    # --- Commercials --------------------------------------------------------
    FactSpec(
        "value.estimated",
        "The estimated contract value in rupees",
        FactType.MONEY,
        "commercials",
        "INR",
    ),
    FactSpec(
        "emd.amount",
        "The earnest money deposit amount in rupees",
        FactType.MONEY,
        "commercials",
        "INR",
    ),
    FactSpec(
        "fee.tender_document",
        "The tender document fee in rupees",
        FactType.MONEY,
        "commercials",
        "INR",
    ),
    FactSpec(
        "security.performance_percent",
        "Performance security as a percentage of contract price",
        FactType.PERCENT,
        "commercials",
        "%",
    ),
    FactSpec(
        "advance.mobilisation_percent",
        "Mobilisation advance as a percentage of contract price",
        FactType.PERCENT,
        "commercials",
        "%",
    ),
    # --- Eligibility --------------------------------------------------------
    FactSpec(
        "eligibility.annual_turnover_minimum",
        "Minimum average annual turnover required, in rupees",
        FactType.MONEY,
        "eligibility",
        "INR",
    ),
    FactSpec(
        "eligibility.net_worth_minimum",
        "Minimum net worth required, in rupees",
        FactType.MONEY,
        "eligibility",
        "INR",
    ),
    FactSpec(
        "eligibility.similar_work_value_minimum",
        "Minimum value of a single similar completed work, in rupees",
        FactType.MONEY,
        "eligibility",
        "INR",
    ),
    FactSpec(
        "eligibility.experience_years_minimum",
        "Minimum years of experience required",
        FactType.INTEGER,
        "eligibility",
        "years",
    ),
    FactSpec(
        "eligibility.contractor_class",
        "The contractor registration class required",
        FactType.TEXT,
        "eligibility",
    ),
    FactSpec(
        "eligibility.jv_allowed",
        "Whether joint ventures or consortia are permitted",
        FactType.BOOLEAN,
        "eligibility",
    ),
    FactSpec(
        "eligibility.subcontracting_percent_max",
        "Maximum percentage of work that may be subcontracted",
        FactType.PERCENT,
        "eligibility",
        "%",
    ),
    # --- Schedule -----------------------------------------------------------
    FactSpec(
        "date.bid_submission",
        "The bid submission deadline",
        FactType.DATE,
        "schedule",
    ),
    FactSpec(
        "date.bid_opening",
        "The date bids will be opened",
        FactType.DATE,
        "schedule",
    ),
    FactSpec(
        "duration.completion_days",
        "The contract completion period in days",
        FactType.DURATION_DAYS,
        "schedule",
        "days",
    ),
    FactSpec(
        "duration.defect_liability_days",
        "The defect liability period in days",
        FactType.DURATION_DAYS,
        "schedule",
        "days",
    ),
    FactSpec(
        "duration.bid_validity_days",
        "How many days the bid must remain valid",
        FactType.DURATION_DAYS,
        "schedule",
        "days",
    ),
    # --- Risk ---------------------------------------------------------------
    FactSpec(
        "risk.liquidated_damages_percent_per_day",
        "Liquidated damages per day as a percentage of contract price",
        FactType.PERCENT,
        "risk",
        "%/day",
    ),
    FactSpec(
        "risk.liquidated_damages_cap_percent",
        "Maximum total liquidated damages as a percentage of contract price",
        FactType.PERCENT,
        "risk",
        "%",
    ),
    FactSpec(
        "risk.price_escalation_allowed",
        "Whether price escalation or adjustment is payable",
        FactType.BOOLEAN,
        "risk",
    ),
    FactSpec(
        "risk.arbitration_seat",
        "Where arbitration is seated",
        FactType.TEXT,
        "risk",
    ),
    FactSpec(
        "risk.retention_percent",
        "Retention money as a percentage of each bill",
        FactType.PERCENT,
        "risk",
        "%",
    ),
)

FACTS_BY_KEY: dict[str, FactSpec] = {fact.key: fact for fact in FACTS}

GROUPS: tuple[str, ...] = tuple(dict.fromkeys(fact.group for fact in FACTS))


def facts_in(group: str) -> tuple[FactSpec, ...]:
    return tuple(fact for fact in FACTS if fact.group == group)


_JSON_TYPE = {
    FactType.MONEY: "number",
    FactType.PERCENT: "number",
    FactType.DURATION_DAYS: "number",
    FactType.INTEGER: "number",
    FactType.BOOLEAN: "boolean",
    FactType.DATE: "string",
    FactType.TEXT: "string",
}


def json_schema_for(group: str) -> dict[str, object]:
    """A strict schema for one group's extraction call.

    Every field is nullable and every field is required. Required-and-nullable
    rather than optional, because a model given the option to omit a key will
    quietly omit the ones it found hardest, and a missing key is
    indistinguishable from "the tender does not say" — which is a distinction
    the decision engine must not lose, since an unknown never counts as a pass.
    """
    properties: dict[str, object] = {}
    for fact in facts_in(group):
        field_name = fact.key.replace(".", "__")
        properties[field_name] = {
            "type": "object",
            "properties": {
                "value": {"type": [_JSON_TYPE[fact.type], "null"]},
                "quote": {"type": ["string", "null"]},
                "page": {"type": ["number", "null"]},
                "confidence": {"type": "number"},
            },
            "required": ["value", "quote", "page", "confidence"],
            "additionalProperties": False,
        }

    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }
