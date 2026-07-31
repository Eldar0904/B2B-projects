"""Regression tests for the scoring-ceiling bug.

The original weights summed to 0.75 because three of the six signals were
never implemented. That made `final_score` unable to exceed 0.75 while the
configured HIGH threshold was 0.95, so hybrid auto-accept could never fire.
Lowering the threshold in `.env` to compensate then caused the opposite
failure: 52.7% of a real 300-row sample fell under the auto-reject
threshold and was discarded without human review.

These tests make that class of mistake impossible to reintroduce silently.
"""

import pytest

from app.services.search.scoring import (
    DEFAULT_WEIGHTS,
    ScoringWeights,
    compute_final_score,
)


def test_default_weights_sum_to_exactly_one():
    assert DEFAULT_WEIGHTS.total() == pytest.approx(1.0)


def test_perfect_match_reaches_score_of_one():
    """The bug in one assertion: a perfect score on every signal used to
    produce 0.75, making any threshold above 0.75 unreachable.
    """
    score = compute_final_score(
        embedding_score=1.0,
        keyword_score=1.0,
        fuzzy_name_score=1.0,
        lexical_overlap_score=1.0,
    )
    assert score == pytest.approx(1.0)


def test_no_signal_scores_zero():
    assert compute_final_score() == pytest.approx(0.0)


def test_configured_high_threshold_is_reachable():
    """Guards against re-creating an impossible threshold."""
    from app.config import settings

    max_possible = compute_final_score(
        embedding_score=1.0, keyword_score=1.0,
        fuzzy_name_score=1.0, lexical_overlap_score=1.0,
    )
    assert settings.high_confidence_threshold <= max_possible, (
        f"HIGH_CONFIDENCE_THRESHOLD={settings.high_confidence_threshold} exceeds the "
        f"maximum achievable score {max_possible}; hybrid auto-accept could never fire."
    )
    assert settings.medium_confidence_threshold <= settings.high_confidence_threshold


def test_score_is_monotonic_in_each_signal():
    base = compute_final_score(embedding_score=0.5, keyword_score=0.5,
                               fuzzy_name_score=0.5, lexical_overlap_score=0.5)
    for field in ("embedding_score", "keyword_score", "fuzzy_name_score", "lexical_overlap_score"):
        kwargs = dict(embedding_score=0.5, keyword_score=0.5,
                      fuzzy_name_score=0.5, lexical_overlap_score=0.5)
        kwargs[field] = 0.9
        assert compute_final_score(**kwargs) > base, f"increasing {field} must increase the score"


def test_custom_weights_are_respected():
    only_overlap = ScoringWeights(
        embedding_score=0.0, keyword_score=0.0,
        fuzzy_name_score=0.0, lexical_overlap_score=1.0,
    )
    score = compute_final_score(
        embedding_score=1.0, keyword_score=1.0,
        fuzzy_name_score=1.0, lexical_overlap_score=0.25,
        weights=only_overlap,
    )
    assert score == pytest.approx(0.25)
