"""
Final ranking: re-score candidates using direct name similarity.

Retriever scores alone (especially embeddings) are unreliable; this stage
caps confidence by how similar the actual product names are.
"""
from typing import List

from app.matching.base import BaseRanker, Candidate
from app.matching.name_match import adjust_candidate_score, anchor_overlap


class HeuristicRanker(BaseRanker):
    def __init__(self, product_lookup: dict):
        self.product_lookup = product_lookup

    def rank(self, item: dict, candidates: List[Candidate], top_n: int) -> List[Candidate]:
        item_text = " ".join(
            p for p in [item.get("item_name") or "", item.get("description") or ""] if p
        ).strip()

        reranked: List[Candidate] = []
        item_category = (item.get("category_code") or "").strip() or None

        for c in candidates:
            product = self.product_lookup.get(c.catalog_product_id)
            if product is None:
                continue

            item_code = (item.get("item_code") or "").strip()
            product_code = (product.code or "").strip()
            exact_code = bool(item_code and product_code and item_code == product_code)

            if c.code_matched and exact_code:
                reranked.append(c)
                continue

            if c.code_matched and not exact_code:
                c = Candidate(
                    catalog_product_id=c.catalog_product_id,
                    score=c.score,
                    explanation=c.explanation,
                    code_matched=False,
                )

            product_text = product.name or ""
            same_category = (
                item_category
                and product.category_code
                and item_category == product.category_code
            )
            if not anchor_overlap(item_text, product_text) and not same_category:
                continue

            adjusted, note = adjust_candidate_score(
                item_text, product_text, c.score, code_matched=False,
            )
            explanation = c.explanation
            if note:
                explanation = f"{explanation}; {note}"

            reranked.append(Candidate(
                catalog_product_id=c.catalog_product_id,
                score=adjusted,
                explanation=explanation,
                code_matched=c.code_matched,
            ))

        reranked.sort(key=lambda x: x.score, reverse=True)
        return reranked[:top_n]
