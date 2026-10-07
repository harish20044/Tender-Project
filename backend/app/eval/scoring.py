"""Comparing an extracted fact against the known answer.

Separated from the harness that produces the facts because the interesting
decisions are all here, and they are decisions rather than mechanics: what
counts as the same money, how close a percentage has to be, whether a page
one off is a hit or a miss. Keeping them in a module with no database or
network lets them be tested directly, which matters — a scorer that is wrong
about what counts flatters or libels the system it measures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.extraction.schema import FACTS_BY_KEY, FactType

# Percentages and per-day rates are small decimals that have been through a
# JSON round trip, so an exact float comparison would fail on 0.05. Money and
# durations are whole numbers and are compared exactly: a rupee figure that is
# "nearly right" is wrong, because it came from either the wrong clause or a
# botched crore conversion.
_PERCENT_TOLERANCE = 1e-6

_PUNCTUATION = re.compile(r"[^\w\s]+")
_WHITESPACE = re.compile(r"\s+")


class Verdict(StrEnum):
    CORRECT = "correct"
    WRONG = "wrong"
    MISSED = "missed"  # stated in the document, but came back null
    UNSCORED = "unscored"  # no expected value annotated for this key


@dataclass(frozen=True)
class FactScore:
    key: str
    verdict: Verdict
    expected: object
    actual: object
    expected_page: int | None = None
    actual_page: int | None = None
    confidence: float = 0.0

    @property
    def page_correct(self) -> bool | None:
        """Whether the citation points at the right page.

        None when there is nothing to judge — either the page was not
        annotated, or the value itself was not found, in which case a page
        verdict would double-count a failure already recorded.
        """
        if self.expected_page is None or self.verdict in (Verdict.MISSED, Verdict.UNSCORED):
            return None
        return self.actual_page == self.expected_page


def normalise_text(value: object) -> str:
    text = str(value).casefold()
    text = _PUNCTUATION.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def values_match(expected: object, actual: object, fact_type: FactType) -> bool:
    """Whether ``actual`` is the same answer as ``expected``."""
    if actual is None:
        return expected is None

    if fact_type is FactType.BOOLEAN:
        # Only a real boolean counts. A model that returns the string "yes"
        # has not filled the field the schema asked for, and a truthiness
        # check here would hide that.
        return isinstance(actual, bool) and actual is expected

    if fact_type is FactType.TEXT:
        want, got = normalise_text(expected), normalise_text(actual)
        if not want or not got:
            return False
        # Containment either way round: the document says "Class-I (Civil)"
        # and the annotation says "Class-I". Both are the same answer, and
        # insisting on one phrasing would measure wording, not reading.
        return want in got or got in want

    if fact_type is FactType.DATE:
        return normalise_text(expected) == normalise_text(actual)

    if isinstance(actual, bool):
        # bool is an int in Python, so True would otherwise compare equal to
        # a numeric 1 and score a percentage field as correct.
        return False
    if not isinstance(actual, int | float):
        return False

    if fact_type is FactType.PERCENT:
        return abs(float(actual) - float(expected)) <= _PERCENT_TOLERANCE  # type: ignore[arg-type]
    return float(actual) == float(expected)  # type: ignore[arg-type]


def score_fact(
    key: str,
    actual: object,
    *,
    expected_facts: dict[str, object],
    expected_pages: dict[str, int],
    actual_page: int | None = None,
    confidence: float = 0.0,
) -> FactScore:
    expected = expected_facts.get(key)
    spec = FACTS_BY_KEY.get(key)

    if key not in expected_facts or spec is None:
        verdict = Verdict.UNSCORED
    elif actual is None:
        verdict = Verdict.MISSED
    elif values_match(expected, actual, spec.type):
        verdict = Verdict.CORRECT
    else:
        verdict = Verdict.WRONG

    return FactScore(
        key=key,
        verdict=verdict,
        expected=expected,
        actual=actual,
        expected_page=expected_pages.get(key),
        actual_page=actual_page,
        confidence=confidence,
    )


@dataclass(frozen=True)
class ExtractionReport:
    """How a single case scored, with the per-fact detail kept."""

    case_key: str
    scores: tuple[FactScore, ...]

    @property
    def scorable(self) -> tuple[FactScore, ...]:
        return tuple(s for s in self.scores if s.verdict is not Verdict.UNSCORED)

    @property
    def correct(self) -> int:
        return sum(1 for s in self.scores if s.verdict is Verdict.CORRECT)

    @property
    def wrong(self) -> int:
        return sum(1 for s in self.scores if s.verdict is Verdict.WRONG)

    @property
    def missed(self) -> int:
        return sum(1 for s in self.scores if s.verdict is Verdict.MISSED)

    @property
    def accuracy(self) -> float:
        """Correct as a share of every fact that should have been found."""
        total = len(self.scorable)
        return self.correct / total if total else 0.0

    @property
    def precision(self) -> float:
        """Correct as a share of the facts actually asserted.

        Reported alongside accuracy because the two fail differently and the
        remedies are opposite: a system that answers nothing scores badly on
        accuracy but perfectly on precision, and one that guesses freely does
        the reverse. A null is an honest abstention, so it is excluded here
        and penalised in accuracy instead.
        """
        asserted = self.correct + self.wrong
        return self.correct / asserted if asserted else 0.0

    @property
    def page_accuracy(self) -> float:
        """Share of correctly-found facts that also cite the right page."""
        judged = [s for s in self.scores if s.page_correct is not None]
        if not judged:
            return 0.0
        return sum(1 for s in judged if s.page_correct) / len(judged)

    @property
    def pages_judged(self) -> int:
        return sum(1 for s in self.scores if s.page_correct is not None)
