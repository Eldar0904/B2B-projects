"""
v1 retriever: TF-IDF cosine similarity over normalized_text.
"""
from typing import Dict, List, Optional, Set

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy.orm import Session

from app.matching.base import BaseRetriever, Candidate
from app.matching.scope import allowed_product_ids, blocked_product_ids, filter_ranked_pairs
from app.models.db_models import CatalogProduct


class TfidfRetriever(BaseRetriever):
    def __init__(self, db: Session, product_ids: Optional[Set[int]] = None):
        self.db = db
        self.product_ids = product_ids
        self._cache: Dict[int, tuple] = {}

    def _build_index(self, source_id: int):
        products = (
            self.db.query(CatalogProduct)
            .filter(CatalogProduct.source_id == source_id)
            .all()
        )
        if self.product_ids is not None:
            products = [p for p in products if p.id in self.product_ids]

        texts = [p.normalized_text or "" for p in products]
        ids = [p.id for p in products]

        vectorizer = TfidfVectorizer(ngram_range=(1, 2), min_df=1)
        if not texts or all(t.strip() == "" for t in texts):
            matrix = None
        else:
            matrix = vectorizer.fit_transform(texts)

        self._cache[source_id] = (vectorizer, matrix, ids)
        return self._cache[source_id]

    def invalidate(self, source_id: int):
        self._cache.pop(source_id, None)

    def get_top_k(self, item: dict, source_id: int, k: int) -> List[Candidate]:
        item_normalized_text = item.get("normalized_text") or ""
        if source_id not in self._cache:
            self._build_index(source_id)

        vectorizer, matrix, product_ids = self._cache[source_id]
        if matrix is None or not item_normalized_text.strip():
            return []

        item_vec = vectorizer.transform([item_normalized_text])
        sims = cosine_similarity(item_vec, matrix)[0]

        ranked = sorted(zip(product_ids, sims), key=lambda x: x[1], reverse=True)
        ranked = filter_ranked_pairs(
            ranked, allowed_product_ids(item), blocked_product_ids(item),
        )[:k]
        return [
            Candidate(
                catalog_product_id=pid,
                score=float(score),
                explanation=f"TF-IDF text similarity score {score:.3f}",
            )
            for pid, score in ranked
            if score > 0
        ]
