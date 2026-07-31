"""Phase 7: train/test split, baseline comparison, and the "deploy only
if performance improves" gate (spec section 21/23).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.services.ml.dataset import TrainingPair
from app.services.ml.model import MatchClassifier, build_model
from app.services.search.scoring import DEFAULT_WEIGHTS, compute_final_score


@dataclass
class EvaluationResult:
    n_total: int
    n_train: int
    n_test: int
    n_positive: int
    n_negative: int
    baseline_auc: float | None
    model_auc: float | None
    improvement: float | None
    should_deploy: bool
    reason: str


def _baseline_score(pair: TrainingPair) -> float:
    """Phase 2's existing linear formula, applied to the same sub-scores -
    i.e. "how would the current, already-shipped scoring do on this
    labeled example, with no training at all."
    """
    return compute_final_score(
        embedding_score=pair.features.embedding_score,
        keyword_score=pair.features.keyword_score,
        fuzzy_name_score=pair.features.fuzzy_name_score,
        weights=DEFAULT_WEIGHTS,
    )


def _safe_auc(y_true: np.ndarray, scores: np.ndarray) -> float | None:
    from sklearn.metrics import roc_auc_score

    if len(set(y_true.tolist())) < 2:
        return None  # AUC is undefined with only one class present
    return float(roc_auc_score(y_true, scores))


def compare_to_baseline(
    pairs: list[TrainingPair],
    *,
    min_examples: int,
    test_split_fraction: float = 0.2,
    min_improvement_margin: float = 0.02,
    model_backend: str = "sklearn_gbm",
) -> EvaluationResult:
    n_total = len(pairs)
    n_positive = sum(1 for p in pairs if p.label == 1)
    n_negative = n_total - n_positive

    if n_total < min_examples:
        return EvaluationResult(
            n_total=n_total,
            n_train=0,
            n_test=0,
            n_positive=n_positive,
            n_negative=n_negative,
            baseline_auc=None,
            model_auc=None,
            improvement=None,
            should_deploy=False,
            reason=(
                f"Only {n_total} labeled examples available; spec section 21 requires "
                f"{min_examples}+ verified matches before training. Need {min_examples - n_total} more."
            ),
        )

    from sklearn.model_selection import train_test_split

    X = np.array([p.features.as_list() for p in pairs])
    y = np.array([p.label for p in pairs])
    baseline_scores = np.array([_baseline_score(p) for p in pairs])

    try:
        X_train, X_test, y_train, y_test, base_train, base_test = train_test_split(
            X, y, baseline_scores, test_size=test_split_fraction, random_state=42, stratify=y
        )
    except ValueError as exc:
        # e.g. a class has too few members to stratify - report rather than crash.
        return EvaluationResult(
            n_total=n_total,
            n_train=0,
            n_test=0,
            n_positive=n_positive,
            n_negative=n_negative,
            baseline_auc=None,
            model_auc=None,
            improvement=None,
            should_deploy=False,
            reason=f"Could not split data for evaluation: {exc}",
        )

    if len(set(y_train.tolist())) < 2:
        # A classifier can't be trained on a single class - e.g. every
        # stored decision so far has been "user_selected" with no
        # negatives at all. Report why rather than letting the model
        # library raise a confusing internal error.
        return EvaluationResult(
            n_total=n_total,
            n_train=len(X_train),
            n_test=len(X_test),
            n_positive=n_positive,
            n_negative=n_negative,
            baseline_auc=None,
            model_auc=None,
            improvement=None,
            should_deploy=False,
            reason="Training data contains only one class (need both matches and non-matches to train a classifier).",
        )

    model: MatchClassifier = build_model(model_backend)
    model.fit(X_train, y_train)
    model_probs = model.predict_proba(X_test)

    baseline_auc = _safe_auc(y_test, base_test)
    model_auc = _safe_auc(y_test, model_probs)

    if baseline_auc is None or model_auc is None:
        return EvaluationResult(
            n_total=n_total,
            n_train=len(X_train),
            n_test=len(X_test),
            n_positive=n_positive,
            n_negative=n_negative,
            baseline_auc=baseline_auc,
            model_auc=model_auc,
            improvement=None,
            should_deploy=False,
            reason="Test split did not contain both classes; AUC undefined. Try a larger dataset.",
        )

    improvement = model_auc - baseline_auc
    should_deploy = improvement >= min_improvement_margin

    return EvaluationResult(
        n_total=n_total,
        n_train=len(X_train),
        n_test=len(X_test),
        n_positive=n_positive,
        n_negative=n_negative,
        baseline_auc=baseline_auc,
        model_auc=model_auc,
        improvement=improvement,
        should_deploy=should_deploy,
        reason=(
            f"Model AUC {model_auc:.4f} vs baseline {baseline_auc:.4f} "
            f"({'+' if improvement >= 0 else ''}{improvement:.4f}); "
            f"{'meets' if should_deploy else 'does not meet'} the {min_improvement_margin} improvement margin."
        ),
    )
