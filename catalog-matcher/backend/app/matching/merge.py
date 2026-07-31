"""
Merge candidate lists from multiple retrievers.

Uses Reciprocal Rank Fusion (RRF) so scores from TF-IDF, fuzzy text, codes,
and embeddings (different scales) can be combined without manual weight tuning.
"""
from typing import Dict, List

from app.matching.base import Candidate


RRF_K = 60


def rrf_merge(candidate_lists: List[List[Candidate]], top_k: int) -> List[Candidate]:
    """Fuse ranked lists; keep the best explanation/score per product id."""
    if not candidate_lists:
        return []

    fused: Dict[int, dict] = {}

    for lst in candidate_lists:
        for rank, cand in enumerate(lst):
            entry = fused.get(cand.catalog_product_id)
            rrf_score = 1.0 / (RRF_K + rank + 1)
            if entry is None:
                fused[cand.catalog_product_id] = {
                    "rrf": rrf_score,
                    "raw_score": cand.score,
                    "explanation": cand.explanation,
                    "code_matched": cand.code_matched,
                }
            else:
                entry["rrf"] += rrf_score
                if cand.score > entry["raw_score"]:
                    entry["raw_score"] = cand.score
                    entry["explanation"] = cand.explanation
                entry["code_matched"] = entry["code_matched"] or cand.code_matched

    ranked = sorted(fused.items(), key=lambda x: x[1]["rrf"], reverse=True)[:top_k]

    # Map RRF back to a 0..1 confidence-like score for UI consistency
    max_rrf = ranked[0][1]["rrf"] if ranked else 1.0

    return [
        Candidate(
            catalog_product_id=pid,
            score=min(1.0, data["raw_score"] if data["raw_score"] > 0 else data["rrf"] / max_rrf),
            explanation=data["explanation"],
            code_matched=data["code_matched"],
        )
        for pid, data in ranked
    ]
