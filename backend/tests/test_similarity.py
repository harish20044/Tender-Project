"""Tests for the similarity signals.

The MinHash path is the one worth pinning hardest: it decides whether a tender
is reported as a probable *reissue* of an earlier one, which is a much
stronger claim than "resembles", and a false positive there would send an
estimator looking for a bidding history that does not exist.

All pure functions — no embeddings, no database.
"""

from __future__ import annotations

from app.similarity.compare import REISSUE_THRESHOLD, jaccard
from app.similarity.corpus import MINHASH_PERMUTATIONS, minhash_signature, shingles

NHAI = (
    "Construction of a four-lane bypass including a major bridge at Km 42.500 "
    "on NH-46, Sehore District, Madhya Pradesh, on EPC Mode"
)
NHAI_REISSUE = (
    "Construction of a four-lane bypass including a major bridge at Km 42.500 "
    "on NH-46, Sehore District, Madhya Pradesh, on EPC Mode (re-tender)"
)
UNRELATED = (
    "Annual rate contract for supply of stationery and printer consumables to divisional offices"
)


# --- shingling ------------------------------------------------------------- #


def test_shingles_are_overlapping_word_windows() -> None:
    assert shingles("a b c d e", size=4) == {"a b c d", "b c d e"}


def test_a_phrase_shorter_than_the_window_is_kept_whole() -> None:
    assert shingles("short title", size=4) == {"short title"}


def test_empty_text_produces_no_shingles() -> None:
    assert shingles("   ") == set()


def test_shingling_ignores_case() -> None:
    assert shingles("Construction Of Road", size=2) == shingles("construction of road", size=2)


# --- signatures ------------------------------------------------------------ #


def test_a_signature_has_one_value_per_permutation() -> None:
    assert len(minhash_signature(NHAI)) == MINHASH_PERMUTATIONS


def test_identical_text_signs_identically() -> None:
    assert minhash_signature(NHAI) == minhash_signature(NHAI)


# --- jaccard --------------------------------------------------------------- #


def test_identical_signatures_are_fully_similar() -> None:
    signature = minhash_signature(NHAI)

    assert jaccard(signature, signature) == 1.0


def test_signatures_of_different_lengths_are_not_compared() -> None:
    assert jaccard([1, 2, 3], [1, 2]) == 0.0
    assert jaccard([], [1, 2]) == 0.0


# --- what the threshold actually decides ----------------------------------- #


def test_a_re_advertised_tender_reads_as_a_probable_reissue() -> None:
    score = jaccard(minhash_signature(NHAI), minhash_signature(NHAI_REISSUE))

    assert score >= REISSUE_THRESHOLD


def test_unrelated_work_is_never_called_a_reissue() -> None:
    score = jaccard(minhash_signature(NHAI), minhash_signature(UNRELATED))

    assert score < REISSUE_THRESHOLD
    assert score < 0.2


def test_similar_but_distinct_work_is_not_called_a_reissue() -> None:
    # Same authority and same kind of work, different project: this is the
    # case the threshold exists to separate, and the one a blunter text
    # measure would get wrong.
    other = (
        "Construction of a six-lane bypass including a minor bridge at Km 118.200 "
        "on NH-52, Dewas District, Madhya Pradesh, on EPC Mode"
    )

    score = jaccard(minhash_signature(NHAI), minhash_signature(other))

    assert score < REISSUE_THRESHOLD
