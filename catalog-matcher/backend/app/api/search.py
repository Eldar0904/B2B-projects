from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import DestinationProduct
from app.services.search.index_manager import get_index
from app.services.search.loader import load_master_records

router = APIRouter(prefix="/api/search", tags=["search"])


class ReindexResult(BaseModel):
    total_records: int
    indexed_records: int
    group_headers_excluded: int
    embedding_dim: int


class CandidateOut(BaseModel):
    master_product_id: str
    embedding_score: float
    keyword_score: float
    fuzzy_name_score: float
    final_score: float
    matched_by: list[str]


@router.post("/reindex", response_model=ReindexResult)
def reindex(db: Session = Depends(get_db)):
    """Rebuild the keyword/fuzzy/vector indexes from the current master
    catalog in the DB. Call this after uploading/re-uploading a master
    catalog, before requesting candidates.
    """
    records = load_master_records(db)
    if not records:
        raise HTTPException(status_code=400, detail="No master products found. Upload a master catalog first.")
    stats = get_index().build(records)
    return ReindexResult(
        total_records=stats.total_records,
        indexed_records=stats.indexed_records,
        group_headers_excluded=stats.group_headers_excluded,
        embedding_dim=stats.embedding_dim,
    )


@router.get("/candidates/{destination_product_id}", response_model=list[CandidateOut])
def get_candidates(destination_product_id: str, top_k: int = 20, db: Session = Depends(get_db)):
    """Spec Phase 2 test: destination product -> top 20 candidates."""
    destination_product = db.get(DestinationProduct, destination_product_id)
    if not destination_product:
        raise HTTPException(status_code=404, detail="Destination product not found")

    index = get_index()
    if not index.is_built:
        raise HTTPException(status_code=409, detail="Search index not built yet. Call POST /api/search/reindex first.")

    query_text = destination_product.normalized_name or destination_product.product_name or ""
    candidates = index.search(query_text, top_k=top_k)
    return [
        CandidateOut(
            master_product_id=c.master_product_id,
            embedding_score=c.embedding_score,
            keyword_score=c.keyword_score,
            fuzzy_name_score=c.fuzzy_name_score,
            final_score=c.final_score,
            matched_by=sorted(c.matched_by),
        )
        for c in candidates
    ]
