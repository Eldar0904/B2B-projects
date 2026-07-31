"""Phase 7: the P(match) classifier (spec section 23).

`MatchClassifier` interface with three implementations selected via
`ML_MODEL_BACKEND`:

- `SklearnGradientBoostingModel` (default) - always installable, no extra
  dependency; a real gradient-boosted trees model.
- `XGBoostModel` / `LightGBMModel` (optional, spec's named choices) -
  lazily imported. Not exercised in this sandbox because `pip download`
  for the xgboost wheel returned zero bytes after repeated attempts here
  (see ARCHITECTURE.md Phase 7) - likely environment-specific, not a
  problem with the code path itself.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class MatchClassifier(ABC):
    @abstractmethod
    def fit(self, X: np.ndarray, y: np.ndarray) -> None: ...

    @abstractmethod
    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Return P(match) for each row, shape (n,)."""


class SklearnGradientBoostingModel(MatchClassifier):
    def __init__(self, **kwargs):
        from sklearn.ensemble import GradientBoostingClassifier

        defaults = {"n_estimators": 100, "max_depth": 3, "random_state": 42}
        defaults.update(kwargs)
        self._model = GradientBoostingClassifier(**defaults)
        self._fitted = False

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        self._model.fit(X, y)
        self._fitted = True

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError("Model has not been trained yet. Call fit() first.")
        return self._model.predict_proba(X)[:, 1]


class XGBoostModel(MatchClassifier):
    def __init__(self, **kwargs):
        try:
            import xgboost as xgb
        except ImportError as exc:  # pragma: no cover - exercised only when misconfigured
            raise ImportError(
                "xgboost is not installed. Run: pip install xgboost, then set "
                "ML_MODEL_BACKEND=xgboost."
            ) from exc
        defaults = {"n_estimators": 100, "max_depth": 3, "random_state": 42, "eval_metric": "logloss"}
        defaults.update(kwargs)
        self._model = xgb.XGBClassifier(**defaults)
        self._fitted = False

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        self._model.fit(X, y)
        self._fitted = True

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError("Model has not been trained yet. Call fit() first.")
        return self._model.predict_proba(X)[:, 1]


class LightGBMModel(MatchClassifier):
    def __init__(self, **kwargs):
        try:
            import lightgbm as lgb
        except ImportError as exc:  # pragma: no cover - exercised only when misconfigured
            raise ImportError(
                "lightgbm is not installed. Run: pip install lightgbm, then set "
                "ML_MODEL_BACKEND=lightgbm."
            ) from exc
        defaults = {"n_estimators": 100, "max_depth": 3, "random_state": 42, "verbose": -1}
        defaults.update(kwargs)
        self._model = lgb.LGBMClassifier(**defaults)
        self._fitted = False

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        self._model.fit(X, y)
        self._fitted = True

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError("Model has not been trained yet. Call fit() first.")
        return self._model.predict_proba(X)[:, 1]


def build_model(backend: str) -> MatchClassifier:
    if backend == "sklearn_gbm":
        return SklearnGradientBoostingModel()
    if backend == "xgboost":
        return XGBoostModel()
    if backend == "lightgbm":
        return LightGBMModel()
    raise ValueError(f"Unknown ml_model_backend: {backend}")
