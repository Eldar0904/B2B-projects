import numpy as np
import pytest

from app.services.ml.model import SklearnGradientBoostingModel, build_model


def _toy_data(n=200, seed=0):
    rng = np.random.default_rng(seed)
    X_pos = rng.normal(loc=1.0, scale=0.2, size=(n // 2, 3))
    X_neg = rng.normal(loc=-1.0, scale=0.2, size=(n // 2, 3))
    X = np.vstack([X_pos, X_neg])
    y = np.array([1] * (n // 2) + [0] * (n // 2))
    return X, y


def test_sklearn_model_learns_a_clearly_separable_toy_problem():
    X, y = _toy_data()
    model = SklearnGradientBoostingModel()
    model.fit(X, y)
    probs = model.predict_proba(X)
    assert probs.shape == (len(X),)
    # Should confidently separate this trivial, well-separated toy data.
    assert (probs[y == 1] > 0.5).mean() > 0.9
    assert (probs[y == 0] < 0.5).mean() > 0.9


def test_predict_before_fit_raises():
    model = SklearnGradientBoostingModel()
    with pytest.raises(RuntimeError):
        model.predict_proba(np.array([[0.0, 0.0, 0.0]]))


def test_build_model_selects_sklearn_backend_by_default():
    model = build_model("sklearn_gbm")
    assert isinstance(model, SklearnGradientBoostingModel)


def test_build_model_rejects_unknown_backend():
    with pytest.raises(ValueError):
        build_model("not_a_real_backend")


def test_xgboost_backend_raises_clear_error_when_not_installed():
    """xgboost isn't installed in this environment (see ARCHITECTURE.md
    Phase 7) - verify the lazy-import guard gives an actionable error
    rather than a confusing one."""
    pytest.importorskip("sys")  # always available; just structuring the skip logic below
    try:
        import xgboost  # noqa: F401
        pytest.skip("xgboost is installed in this environment; import-guard path not exercised")
    except ImportError:
        with pytest.raises(ImportError, match="xgboost"):
            build_model("xgboost")
