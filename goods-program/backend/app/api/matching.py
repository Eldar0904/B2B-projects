from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import DestinationProduct, Upload
from app.services import matching
from app.services.search.index_manager import get_index

router = APIRouter(prefix="/api/matching", tags=["matching"])


class ProgressOut(BaseModel):
    total: int
    pending: int
    matched: int
    no_match: int


class FeedbackStatsOut(BaseModel):
    total: int
    user_selected: int
    manual_search_selected: int
    no_match: int
    auto_accepted: int
    user_rejected: int
    auto_rejected: int


class AutoMatchOut(BaseModel):
    checked: int
    auto_matched: int
    exact_matches: int
    threshold_matches: int
    auto_rejected: int
    still_pending: int


class PrioritizeOut(BaseModel):
    checked: int
    computed: int
    insufficient_candidates: int


class CandidateOut(BaseModel):
    master_product_id: str
    external_id: str | None
    product_name: str | None
    # Full catalog description ("Описание" column). Shown under each
    # candidate in the review UI: the catalog's names are often truncated
    # or generic ("Стол рабочий на 4 детей"), and the detail that actually
    # decides a match - dimensions, material, class - lives here. Without
    # it a reviewer has to open the source spreadsheet to judge.
    description: str | None
    unit: str | None
    price: float | None
    final_score: float
    embedding_score: float
    keyword_score: float
    fuzzy_name_score: float
    lexical_overlap_score: float
    matched_by: list[str]
    explanation: list[str]


class NextProductOut(BaseModel):
    destination_product_id: str
    product_name: str | None
    # The requested item's own description, so the reviewer can compare
    # like with like instead of comparing a bare name against a full spec.
    description: str | None
    quantity: float | None
    price: float | None
    candidates: list[CandidateOut]
    confidence_level: str  # "high" | "medium" | "low", per spec section 16
    no_reliable_match: bool


class ConfirmIn(BaseModel):
    destination_product_id: str
    master_product_id: str
    rank: int = 1


class RejectIn(BaseModel):
    destination_product_id: str


class ManualSearchIn(BaseModel):
    query: str
    top_k: int = 10


def _get_upload_or_404(db: Session, upload_id: str) -> Upload:
    upload = db.get(Upload, upload_id)
    if not upload or upload.upload_type != "destination":
        raise HTTPException(status_code=404, detail="Destination upload not found")
    return upload


def _require_index():
    index = get_index()
    if not index.is_built:
        raise HTTPException(status_code=409, detail="Search index not built yet. Call POST /api/search/reindex first.")
    return index


@router.post("/{upload_id}/start", response_model=ProgressOut)
def start(upload_id: str, db: Session = Depends(get_db)):
    _get_upload_or_404(db, upload_id)
    _require_index()
    progress = matching.get_progress(db, upload_id)
    return ProgressOut(**progress.__dict__)


@router.get("/{upload_id}/progress", response_model=ProgressOut)
def progress(upload_id: str, db: Session = Depends(get_db)):
    _get_upload_or_404(db, upload_id)
    p = matching.get_progress(db, upload_id)
    return ProgressOut(**p.__dict__)


@router.get("/{upload_id}/feedback-stats", response_model=FeedbackStatsOut)
def feedback_stats(upload_id: str, db: Session = Depends(get_db)):
    """Phase 4: counts of stored decisions by type for this upload - a
    precursor to Phase 7's "500+ verified matches" training threshold.
    """
    _get_upload_or_404(db, upload_id)
    stats = matching.get_feedback_stats(db, upload_id)
    return FeedbackStatsOut(**stats.__dict__)


@router.post("/{upload_id}/auto-match", response_model=AutoMatchOut)
def auto_match(upload_id: str, db: Session = Depends(get_db)):
    """Phase 5: explicit, opt-in batch pass over every pending destination
    product - auto-accepts exact matches (spec section 11) and any hybrid
    candidate above HIGH_CONFIDENCE_THRESHOLD (spec section 16), leaves
    everything else pending for human review. Never runs automatically on
    ingest/reindex - the caller decides when to enable automation.
    """
    _get_upload_or_404(db, upload_id)
    index = _require_index()

    low_threshold = settings.low_confidence_threshold if settings.enable_low_confidence_auto_reject else None
    result = matching.run_auto_match_batch(db, index, upload_id, settings.high_confidence_threshold, low_threshold)
    db.commit()
    return AutoMatchOut(**result.__dict__)


@router.post("/{upload_id}/prioritize", response_model=PrioritizeOut)
def prioritize(upload_id: str, db: Session = Depends(get_db)):
    """Phase 8: explicit, opt-in batch pass over every pending destination
    product - computes and stores `uncertainty_margin` (the gap between
    the top-2 reranked candidates) so `GET /next?strategy=uncertainty` can
    surface the most ambiguous cases first, per spec section 24.
    """
    _get_upload_or_404(db, upload_id)
    index = _require_index()

    result = matching.prioritize_batch(db, index, upload_id)
    db.commit()
    return PrioritizeOut(**result.__dict__)


@router.get("/{upload_id}/next", response_model=NextProductOut | None)
def next_product(upload_id: str, strategy: str = "sequential", db: Session = Depends(get_db)):
    _get_upload_or_404(db, upload_id)
    index = _require_index()

    dp = matching.get_next_pending(db, upload_id, strategy=strategy)
    if dp is None:
        return None

    candidates = matching.get_top_candidates(db, index, dp, top_k=3)
    top_score = candidates[0].candidate.final_score if candidates else 0.0
    confidence_level = matching.classify_confidence(
        top_score, settings.high_confidence_threshold, settings.medium_confidence_threshold
    )

    return NextProductOut(
        destination_product_id=dp.id,
        product_name=dp.product_name,
        description=dp.description,
        quantity=dp.quantity,
        price=dp.price,
        confidence_level=confidence_level,
        no_reliable_match=confidence_level == "low",
        candidates=[
            CandidateOut(
                master_product_id=c.master_product.id,
                external_id=c.master_product.external_id,
                product_name=c.master_product.product_name,
                description=c.master_product.description,
                unit=c.master_product.unit,
                price=c.master_product.price,
                final_score=c.candidate.final_score,
                embedding_score=c.candidate.embedding_score,
                keyword_score=c.candidate.keyword_score,
                fuzzy_name_score=c.candidate.fuzzy_name_score,
                lexical_overlap_score=c.candidate.lexical_overlap_score,
                matched_by=sorted(c.candidate.matched_by),
                explanation=c.explanation,
            )
            for c in candidates
        ],
    )


@router.post("/{upload_id}/confirm")
def confirm(upload_id: str, body: ConfirmIn, db: Session = Depends(get_db)):
    _get_upload_or_404(db, upload_id)
    index = _require_index()

    dp = db.get(DestinationProduct, body.destination_product_id)
    if not dp or dp.upload_id != upload_id:
        raise HTTPException(status_code=404, detail="Destination product not found in this upload")

    # Re-fetch a wider candidate pool so the stored feedback's candidate
    # list is complete even if the user picked something via manual search
    # (rank=0) rather than one of the top-3 shown for this product.
    candidates = [c.candidate for c in matching.get_top_candidates(db, index, dp, top_k=20)]
    match = matching.confirm_match(db, dp, body.master_product_id, body.rank, candidates)
    db.commit()
    return {"match_id": match.id, "status": dp.status}


@router.post("/{upload_id}/reject")
def reject(upload_id: str, body: RejectIn, db: Session = Depends(get_db)):
    _get_upload_or_404(db, upload_id)
    index = _require_index()

    dp = db.get(DestinationProduct, body.destination_product_id)
    if not dp or dp.upload_id != upload_id:
        raise HTTPException(status_code=404, detail="Destination product not found in this upload")

    # Record what was shown (and rejected) for traceability, per spec
    # section 20 ("store the complete candidate set when possible").
    candidates = [c.candidate for c in matching.get_top_candidates(db, index, dp, top_k=3)]
    matching.reject_match(db, dp, candidates)
    db.commit()
    return {"status": dp.status}


@router.post("/{upload_id}/manual-search", response_model=list[CandidateOut])
def manual_search(upload_id: str, body: ManualSearchIn, db: Session = Depends(get_db)):
    _get_upload_or_404(db, upload_id)
    index = _require_index()

    scored = index.search(body.query, top_k=body.top_k)
    from app.models import MasterProduct

    out = []
    for c in scored:
        mp = db.get(MasterProduct, c.master_product_id)
        if mp is None:
            continue
        out.append(
            CandidateOut(
                master_product_id=mp.id,
                external_id=mp.external_id,
                product_name=mp.product_name,
                description=mp.description,
                unit=mp.unit,
                price=mp.price,
                final_score=c.final_score,
                embedding_score=c.embedding_score,
                keyword_score=c.keyword_score,
                fuzzy_name_score=c.fuzzy_name_score,
                lexical_overlap_score=c.lexical_overlap_score,
                matched_by=sorted(c.matched_by),
                explanation=matching.build_explanation(c),
            )
        )
    return out
