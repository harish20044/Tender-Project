"""Measuring whether extraction and retrieval are actually right.

A golden set of tenders whose answers are known, run through the production
pipeline and scored on three things that fail independently: whether the
value is right, whether the citation points at the page the value is on, and
whether a question the document cannot answer is declined rather than
invented.
"""

from app.eval.golden import CASES, CASES_BY_KEY, GoldenCase, render_pdf
from app.eval.harness import AbstentionReport, CaseResult, run_all, run_all_sync, run_case
from app.eval.report import as_dict, as_json, render
from app.eval.scoring import ExtractionReport, FactScore, Verdict, score_fact, values_match

__all__ = [
    "CASES",
    "CASES_BY_KEY",
    "AbstentionReport",
    "CaseResult",
    "ExtractionReport",
    "FactScore",
    "GoldenCase",
    "Verdict",
    "as_dict",
    "as_json",
    "render",
    "render_pdf",
    "run_all",
    "run_all_sync",
    "run_case",
    "score_fact",
    "values_match",
]
