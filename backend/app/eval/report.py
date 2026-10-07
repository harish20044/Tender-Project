"""Rendering a run as text a human will actually read.

The per-fact table is the point. An aggregate accuracy figure tells you
whether to worry; only the rows tell you what to fix, and the difference
between a wrong value and a wrong page is the difference between a prompt
problem and a chunking problem.
"""

from __future__ import annotations

import json
from typing import Any

from app.eval.harness import CaseResult
from app.eval.scoring import Verdict

_MARK = {
    Verdict.CORRECT: "ok  ",
    Verdict.WRONG: "WRONG",
    Verdict.MISSED: "MISS",
    Verdict.UNSCORED: "-   ",
}


def _short(value: object, width: int = 22) -> str:
    if value is None:
        return "null"
    text = str(value)
    return text if len(text) <= width else text[: width - 1] + "~"


def render_case(result: CaseResult) -> str:
    report = result.extraction
    lines = [
        "",
        f"case: {result.case_key}   ({result.pages} pages, {result.chunks} chunks)",
        f"tender: {result.tender_id}",
        "",
        f"  {'':5} {'fact':44} {'expected':23} {'extracted':23} page  conf",
        f"  {'-' * 103}",
    ]

    for score in report.scores:
        if score.page_correct is None:
            page = "-"
        elif score.page_correct:
            page = f"ok {score.actual_page}"
        else:
            page = f"!{score.actual_page}/{score.expected_page}"
        lines.append(
            f"  {_MARK[score.verdict]:5} {score.key:44} "
            f"{_short(score.expected):23} {_short(score.actual):23} "
            f"{page:5} {score.confidence:.2f}"
        )

    total = len(report.scorable)
    lines += [
        "",
        f"  value accuracy   {report.accuracy:6.1%}   "
        f"({report.correct}/{total} correct, {report.wrong} wrong, {report.missed} missed)",
        f"  value precision  {report.precision:6.1%}   "
        f"(of {report.correct + report.wrong} asserted)",
        f"  page accuracy    {report.page_accuracy:6.1%}   "
        f"(of {report.pages_judged} citations judged)",
    ]

    abstention = result.abstention
    if abstention.questions:
        lines += [
            "",
            f"  abstention       {abstention.rate:6.1%}   "
            f"({len(abstention.abstained)}/{len(abstention.questions)} "
            "unanswerable questions correctly declined)",
        ]
        for question, text in abstention.answered:
            lines += [f"    invented: {question}", f"              -> {_short(text, 80)}"]

    return "\n".join(lines)


def render(results: list[CaseResult]) -> str:
    parts = [render_case(result) for result in results]

    if len(results) > 1:
        correct = sum(r.extraction.correct for r in results)
        total = sum(len(r.extraction.scorable) for r in results)
        parts += [
            "",
            "=" * 105,
            f"  {len(results)} cases   value accuracy {correct / total if total else 0:.1%} "
            f"({correct}/{total})",
        ]

    return "\n".join(parts)


def as_dict(results: list[CaseResult]) -> dict[str, Any]:
    """Machine-readable form, for tracking runs over time."""
    return {
        "cases": [
            {
                "case": r.case_key,
                "tender_id": r.tender_id,
                "pages": r.pages,
                "chunks": r.chunks,
                "value_accuracy": round(r.extraction.accuracy, 4),
                "value_precision": round(r.extraction.precision, 4),
                "page_accuracy": round(r.extraction.page_accuracy, 4),
                "abstention_rate": round(r.abstention.rate, 4),
                "facts": [
                    {
                        "key": s.key,
                        "verdict": s.verdict.value,
                        "expected": s.expected,
                        "actual": s.actual,
                        "expected_page": s.expected_page,
                        "actual_page": s.actual_page,
                        "page_correct": s.page_correct,
                        "confidence": round(s.confidence, 3),
                    }
                    for s in r.extraction.scores
                ],
            }
            for r in results
        ]
    }


def as_json(results: list[CaseResult]) -> str:
    return json.dumps(as_dict(results), indent=2, default=str)
