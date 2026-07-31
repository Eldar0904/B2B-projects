import numpy as np

from app.services.ml.dataset import TrainingPair
from app.services.ml.evaluate import compare_to_baseline
from app.services.ml.features import FeatureVector


def _make_pair(i, label, rng):
    if label == 1:
        f = FeatureVector(
            embedding_score=rng.uniform(0.8, 1.0),
            keyword_score=rng.uniform(0.8, 1.0),
            fuzzy_name_score=rng.uniform(0.8, 1.0),
            price_difference=rng.uniform(0.0, 0.05),
            price_available=1.0,
        )
    else:
        f = FeatureVector(
            embedding_score=rng.uniform(0.0, 0.2),
            keyword_score=rng.uniform(0.0, 0.2),
            fuzzy_name_score=rng.uniform(0.0, 0.2),
            price_difference=rng.uniform(0.5, 1.0),
            price_available=1.0,
        )
    return TrainingPair(
        destination_product_id=f"d{i}",
        master_product_id=f"m{i}",
        label=label,
        features=f,
        source_decision_type="user_selected" if label else "no_match",
    )


def test_below_threshold_refuses_to_train():
    rng = np.random.default_rng(0)
    pairs = [_make_pair(i, 1 if i % 3 == 0 else 0, rng) for i in range(50)]

    result = compare_to_baseline(pairs, min_examples=500)
    assert result.should_deploy is False
    assert result.model_auc is None
    assert "500" in result.reason or "450" in result.reason  # mentions the gap


def test_clearly_separable_data_trains_and_evaluates(): 
    rng = np.random.default_rng(1)
    pairs = [_make_pair(i, 1 if i % 3 == 0 else 0, rng) for i in range(600)]

    result = compare_to_baseline(pairs, min_examples=500, min_improvement_margin=0.0)
    assert result.n_total == 600
    assert result.baseline_auc is not None
    assert result.model_auc is not None
    assert 0.0 <= result.baseline_auc <= 1.0
    assert 0.0 <= result.model_auc <= 1.0


def test_single_class_dataset_handled_gracefully():
    rng = np.random.default_rng(2)
    pairs = [_make_pair(i, 1, rng) for i in range(600)]  # all positive, no negatives

    result = compare_to_baseline(pairs, min_examples=500)
    assert result.should_deploy is False
    # Should not raise, and should explain why rather than silently lying.
    assert result.reason
