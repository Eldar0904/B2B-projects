"""
Semantic retriever using precomputed product embeddings.
"""
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy.orm import Session

from app.matching.base import BaseRetriever, Candidate
from app.matching.scope import allowed_product_ids, blocked_product_ids, filter_ranked_pairs
from app.models.db_models import CatalogProduct
from app.services.embedding import encode_texts, parse_embedding


class EmbeddingRetriever(BaseRetriever):
    def __init__(
        self,
        db: Session,
        model_name: str,
        product_ids: Optional[Set[int]] = None,
    ):
        self.db = db
        self.model_name = model_name
        self.product_ids = product_ids
        self._cache: Dict[int, Tuple[np.ndarray, List[int]]] = {}
        self._query_cache: Dict[str, np.ndarray] = {}

    def _build_index(self, source_id: int):
        products = (
            self.db.query(CatalogProduct)
            .filter(CatalogProduct.source_id == source_id)
            .all()
        )
        if self.product_ids is not None:
            products = [p for p in products if p.id in self.product_ids]

        ids: List[int] = []
        rows: List[np.ndarray] = []
        for p in products:
            vec = parse_embedding(p.embedding_json)
            if vec is not None:
                ids.append(p.id)
                rows.append(vec)

        matrix = np.vstack(rows) if rows else None
        self._cache[source_id] = (matrix, ids)

    def invalidate(self, source_id: int):
        self._cache.pop(source_id, None)

    def get_top_k(self, item: dict, source_id: int, k: int) -> List[Candidate]:
        if source_id not in self._cache:
            self._build_index(source_id)

        matrix, product_ids = self._cache[source_id]
        if matrix is None or len(product_ids) == 0:
            return []

        query_parts = [
            item.get("item_name") or "",
            item.get("description") or "",
            item.get("item_code") or "",
        ]
        query_text = " ".join(p for p in query_parts if p).strip()
        if not query_text:
            query_text = item.get("normalized_text") or ""
        if not query_text.strip():
            return []

        if query_text not in self._query_cache:
            self._query_cache[query_text] = encode_texts([query_text], self.model_name)[0]
        query_vec = self._query_cache[query_text].reshape(1, -1)
        sims = cosine_similarity(query_vec, matrix)[0]

        ranked = sorted(zip(product_ids, sims), key=lambda x: x[1], reverse=True)
        ranked = filter_ranked_pairs(
            ranked, allowed_product_ids(item), blocked_product_ids(item),
        )[:k]
        return [
            Candidate(
                catalog_product_id=pid,
                score=float(score),
                explanation=f"semantic embedding similarity {score:.3f}",
            )
            for pid, score in ranked
            if score > 0
        ]
