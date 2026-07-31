from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.services.ml.dataset import build_training_pairs
from app.services.ml.evaluate import compare_to_baseline
from app.services.search.index_manager import get_index

router = APIRouter(prefix="/api/ml", tags=["ml"])


class TrainingReadinessOut(BaseModel):
    total_examples: int
    positive_examples: int
    negative_examples: int
    min_required: int
    ready: bool
    examples_needed: int


class TrainResultOut(BaseModel):
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


def _require_index():
    index = get_index()
    if not index.is_built:
        raise HTTPException(status_code=409, detail="Search index not built yet. Call POST /api/search/reindex first.")
    return index


@router.get("/training-readiness", response_model=TrainingReadinessOut)
def training_readiness(upload_id: str | None = None, db: Session = Depends(get_db)):
    """Spec section 21: report progress toward the 500+ verified matches
    threshold before training is even attempted.
    """
    index = _require_index()
    pairs = build_training_pairs(db, index, upload_id=upload_id)
    total = len(pairs)
    positive = sum(1 for p in pairs if p.label == 1)
    min_required = settings.ml_training_min_examples
    return TrainingReadinessOut(
        total_examples=total,
        positive_examples=positive,
        negative_examples=total - positive,
        min_required=min_required,
        ready=total >= min_required,
        examples_needed=max(0, min_required - total),
    )


@router.post("/train", response_model=TrainResultOut)
def train(upload_id: str | None = None, db: Session = Depends(get_db)):
    """Spec section 21/23: build the training dataset from stored
    feedback, train the configured model, evaluate against the Phase 2
    baseline, and report whether it clears the deploy bar. Never trains
    below ML_TRAINING_MIN_EXAMPLES (reported via `reason` instead).
    Training a model here does NOT wire it into live scoring - see
    ARCHITECTURE.md Phase 7 for why that's a deliberate, separate step.
    """
    index = _require_index()
    pairs = build_training_pairs(db, index, upload_id=upload_id)

    result = compare_to_baseline(
        pairs,
        min_examples=settings.ml_training_min_examples,
        test_split_fraction=settings.ml_test_split_fraction,
        min_improvement_margin=settings.ml_min_improvement_margin,
        model_backend=settings.ml_model_backend,
    )
    return TrainResultOut(**result.__dict__)
