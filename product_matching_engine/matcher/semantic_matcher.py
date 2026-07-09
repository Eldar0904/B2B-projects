"""
semantic_matcher.py

Retrieves the Top-N semantic candidates from a catalog for each query item,
using cosine similarity over embeddings produced by `embeddings.embedding_service`.

This module is catalog-agnostic: it operates on plain lists of (code, text)
pairs, so it works identically whether the catalog is the Government
Catalog or a future Supplier Catalog. All catalog-specific column mapping
happens upstream in `importer.excel_reader`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from embeddings.embedding_service import EmbeddingService


@dataclass
class Candidate:
    catalog_code: str
    similarity: float
    index: int  # row index into the catalog DataFrame, for downstream lookups


class SemanticMatcher:
    """Builds a shared embedding space over a catalog and retrieves the
    top-K most similar catalog rows for arbitrary query texts.
    """

    def __init__(self, embedding_service: EmbeddingService | None = None):
        self.embedding_service = embedding_service or EmbeddingService()
        self._catalog_codes: list[str] = []
        self._catalog_vectors: np.ndarray | None = None

    def index_catalog(self, catalog_codes: list[str], catalog_texts: list[str]) -> None:
        """Fit the embedding backend on the catalog corpus and cache the
        resulting catalog vectors for repeated querying.
        """
        self.embedding_service.fit(catalog_texts)
        self._catalog_codes = list(catalog_codes)
        self._catalog_vectors = self.embedding_service.encode(catalog_texts)

    def top_k(self, query_text: str, k: int = 20) -> list[Candidate]:
        if self._catalog_vectors is None:
            raise RuntimeError("index_catalog() must be called before top_k()")

        query_vector = self.embedding_service.encode([query_text])[0]
        similarities = self._catalog_vectors @ query_vector
        # (vectors are L2-normalized in the embedding backends, so the dot
        # product above is already cosine similarity)

        k = min(k, len(similarities))
        top_idx = np.argpartition(-similarities, k - 1)[:k]
        top_idx = top_idx[np.argsort(-similarities[top_idx])]

        return [
            Candidate(
                catalog_code=self._catalog_codes[i],
                similarity=float(similarities[i]),
                index=int(i),
            )
            for i in top_idx
        ]
