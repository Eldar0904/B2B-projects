"""
Fuzzy text retrieval over product names.

Uses a conservative scorer (not plain token_set_ratio) to avoid 100% matches
on generic header words like «Мебель».
"""
from typing import List

from app.matching.base import BaseRetriever, Candidate
from app.matching.scope import allowed_product_ids, blocked_product_ids, filter_candidates
from app.matching.text_similarity import fuzzy_name_score
from app.models.db_models import CatalogProduct


class FuzzyTextRetriever(BaseRetriever):
    def __init__(
        self,
        products: List[CatalogProduct],
        score_cutoff: float = 0.55,
    ):
        self.score_cutoff = score_cutoff
        self._choices: dict[int, str] = {}
        for p in products:
            parts = [p.name or "", p.brand or "", p.model or ""]
            label = " ".join(x for x in parts if x).strip()
            if label:
                self._choices[p.id] = label

    def get_top_k(self, item: dict, source_id: int, k: int) -> List[Candidate]:
        query_parts = [
            item.get("item_name") or "",
            item.get("description") or "",
        ]
        query = " ".join(p for p in query_parts if p).strip()
        if not query or not self._choices:
            return []

        allowed = allowed_product_ids(item)
        blocked = blocked_product_ids(item)
        limit = k * 5 if (allowed is not None or blocked) else k * 2

        scored: List[tuple] = []
        for pid, label in self._choices.items():
            if blocked and pid in blocked:
                continue
            if allowed is not None and pid not in allowed:
                continue
            score = fuzzy_name_score(query, label)
            if score >= self.score_cutoff:
                scored.append((pid, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        candidates = [
            Candidate(
                catalog_product_id=pid,
                score=score,
                explanation=f"fuzzy name match ({score:.3f})",
            )
            for pid, score in scored[:limit]
        ]
        return filter_candidates(candidates, allowed, blocked)[:k]
