"""Putting a computed decision into prose.

The model writes the explanation and nothing else. It is given the scorecard
after the verdict is already decided, and it is told so — the recommendation
is not up for negotiation, and a narration that argued with it would be worse
than useless.

This is the only place a model touches the decision path, and it touches it
strictly downstream.
"""

from __future__ import annotations

import json

from app.core.logging import get_logger
from app.db.models import GateOutcome
from app.providers.base import ChatMessage, ChatProvider

logger = get_logger(__name__)

_SYSTEM = """You explain a bid/no-bid recommendation that has already been \
decided by a rules engine.

Write 3-5 sentences for a construction estimator.

Rules:
- The recommendation is fixed. Explain it; never dispute or hedge it.
- Lead with the reason it came out this way — usually the deciding gate.
- Name the specific figures from the gates. Use the tender's own numbers.
- Mention what could not be determined, if anything, and why that matters.
- No preamble, no bullet points, no restating these instructions."""


def _scorecard(decision: object) -> str:
    gates = []
    for gate in decision.gates:  # type: ignore[attr-defined]
        gates.append(
            {
                "gate": gate.label,
                "outcome": str(gate.outcome),
                "mandatory": gate.mandatory,
                "detail": gate.detail,
            }
        )
    payload = {
        "recommendation": str(decision.recommendation),  # type: ignore[attr-defined]
        "score": decision.score,  # type: ignore[attr-defined]
        "deciding_gate": decision.deciding_gate,  # type: ignore[attr-defined]
        "gates": gates,
        "risks": [
            {"category": r.category, "severity": str(r.severity), "summary": r.summary}
            for r in decision.risks  # type: ignore[attr-defined]
        ],
    }
    return json.dumps(payload, indent=2, default=str)


async def narrate(decision: object, *, chat: ChatProvider) -> str:
    """Explain the decision in prose. Returns a plain fallback if the model fails."""
    try:
        completion = await chat.complete(
            [
                ChatMessage(role="system", content=_SYSTEM),
                ChatMessage(role="user", content=f"Scorecard:\n{_scorecard(decision)}"),
            ],
            max_tokens=700,
        )
        text = (completion.text or "").strip()
        if text:
            return text
    except Exception as exc:
        # A narration failure must not take the decision with it: the verdict
        # is already computed and is the part that matters.
        logger.warning("narration_failed", error=str(exc))

    failed = [g.label for g in decision.gates if g.outcome is GateOutcome.FAIL]  # type: ignore[attr-defined]
    unknown = [g.label for g in decision.gates if g.outcome is GateOutcome.UNKNOWN]  # type: ignore[attr-defined]
    parts = [
        f"{decision.recommendation} at a score of {decision.score}."  # type: ignore[attr-defined]
    ]
    if failed:
        parts.append(f"Failed: {', '.join(failed)}.")
    if unknown:
        parts.append(f"Could not be determined: {', '.join(unknown)}.")
    return " ".join(parts)
