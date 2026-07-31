"""
Code-based retrieval — runs before / alongside text retrievers.

Exact and fuzzy code matches are strong signals and must not depend on
TF-IDF putting the product in the top-K first.
"""
from typing import Dict, List

from rapidfuzz import fuzz, process

from app.matching.base import BaseRetriever, Candidate
from app.matching.scope import allowed_product_ids, blocked_product_ids, filter_candidates
from app.models.db_models import CatalogProduct
from app.services.category import looks_like_catalog_code


class CodeRetriever(BaseRetriever):
    def __init__(
        self,
        products: List[CatalogProduct],
        fuzzy_threshold: float = 0.80,
    ):
        self.fuzzy_threshold = fuzzy_threshold
        self._code_to_ids: Dict[str, List[int]] = {}
        self._codes: List[str] = []

        for p in products:
            code = (p.code or "").strip()
            if not code:
                continue
            self._code_to_ids.setdefault(code, []).append(p.id)
            if code not in self._codes:
                self._codes.append(code)

    def get_top_k(self, item: dict, source_id: int, k: int) -> List[Candidate]:
        item_code = (item.get("item_code") or "").strip()
        if not item_code:
            return []

        seen: Dict[int, Candidate] = {}

        for pid in self._code_to_ids.get(item_code, []):
            seen[pid] = Candidate(
                catalog_product_id=pid,
                score=0.95,
                explanation="exact code match",
                code_matched=True,
            )

        if self._codes and len(seen) < k and looks_like_catalog_code(item_code):
            fuzzy_hits = process.extract(
                item_code,
                self._codes,
                scorer=fuzz.ratio,
                score_cutoff=self.fuzzy_threshold * 100,
                limit=k,
            )
            for matched_code, ratio, _idx in fuzzy_hits:
                for pid in self._code_to_ids.get(matched_code, []):
                    if pid in seen:
                        continue
                    score = min(0.90, 0.70 + (ratio / 100.0) * 0.20)
                    seen[pid] = Candidate(
                        catalog_product_id=pid,
                        score=score,
                        explanation=f"similar code ({ratio / 100.0:.2f}): {matched_code}",
                        code_matched=True,
                    )

        ranked = sorted(seen.values(), key=lambda c: c.score, reverse=True)
        return filter_candidates(
            ranked, allowed_product_ids(item), blocked_product_ids(item),
        )[:k]
