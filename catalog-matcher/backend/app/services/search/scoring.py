"""Candidate scoring.

Weights are plain config, not hardcoded assumptions - they are meant to be
tuned against evaluation results rather than guessed at.

--- The score-ceiling bug this file used to have ------------------------

The previous weights were:

    embedding 0.35 + keyword 0.25 + fuzzy 0.15
      + category 0.10 + attribute 0.10 + identifier 0.05

but category, attribute and identifier scoring were never implemented, so
those three always contributed exactly 0.0. That capped `final_score` at
0.75 - a *perfect* match on every implemented signal still scored 0.75.

Meanwhile the configured thresholds were HIGH = 0.95 and MEDIUM = 0.70.
0.95 was mathematically unreachable, so the hybrid auto-accept path could
never fire even once; the only automation that ever worked was the
separate exact-string-match shortcut, which covers 2.8% of the real
destination file. The user's `.env` had been hand-lowered to HIGH = 0.60 /
LOW = 0.40 to compensate, which then caused the opposite failure: on a
300-row sample, 52.7% of items scored below 0.40 and were silently
auto-rejected as "no match" without a human ever seeing them. That is the
"it didn't really match the two Excels" symptom.

--- The fix -------------------------------------------------------------

Weights now cover only implemented signals and sum to exactly 1.0, so
`final_score` spans the full [0, 1] range and a threshold of 0.95 means
what it says. `total()` is asserted at import time so this class of bug
cannot silently return.

The unimplemented category/attribute/identifier weights are gone rather
than zeroed. Keeping a weight for a signal that is always 0.0 does not
reserve headroom for a future feature - it just deducts that fraction from
every score forever.

Weight rationale, from the measured contribution of each signal on the
real files (see `lexical_overlap.py` for why coverage earns the largest
share):

    lexical_overlap 0.30  answers "is the query's content accounted for?"
    embedding       0.30  the only cross-lingual signal (Kazakh <-> Russian)
    keyword         0.20  precise term matching, now absolutely normalized
    fuzzy           0.20  typo and word-order tolerance
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ScoringWeights:
    embedding_score: float = 0.30
    keyword_score: float = 0.20
    fuzzy_name_score: float = 0.20
    lexical_overlap_score: float = 0.30

    def total(self) -> float:
        return (
            self.embedding_score
            + self.keyword_score
            + self.fuzzy_name_score
            + self.lexical_overlap_score
        )


DEFAULT_WEIGHTS = ScoringWeights()

# Guard the exact bug described above: if someone retunes these and the
# total drifts from 1.0, every configured threshold silently changes
# meaning. Fail loudly at import instead.
assert abs(DEFAULT_WEIGHTS.total() - 1.0) < 1e-9, (
    f"ScoringWeights must sum to 1.0, got {DEFAULT_WEIGHTS.total()}. "
    "A total below 1.0 makes high thresholds unreachable; above 1.0 makes "
    "final_score exceed 1.0 and breaks confidence display."
)


def compute_final_score(
    *,
    embedding_score: float = 0.0,
    keyword_score: float = 0.0,
    fuzzy_name_score: float = 0.0,
    lexical_overlap_score: float = 0.0,
    weights: ScoringWeights = DEFAULT_WEIGHTS,
) -> float:
    """Weighted sum of the four implemented retrieval signals.

    Returns a value in [0, 1] given sub-scores in [0, 1], because the
    weights sum to 1.0.
    """
    return (
        weights.embedding_score * embedding_score
        + weights.keyword_score * keyword_score
        + weights.fuzzy_name_score * fuzzy_name_score
        + weights.lexical_overlap_score * lexical_overlap_score
    )
