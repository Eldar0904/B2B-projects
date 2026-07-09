"""
embedding_service.py

Generates "semantic" vector embeddings for product text.

Backend strategy
-----------------
Two backends are provided behind a common `EmbeddingBackend` interface:

- `TfidfEmbeddingBackend` (default): TF-IDF over word + character n-grams,
  dimensionality-reduced with TruncatedSVD (a form of Latent Semantic
  Analysis). This runs fully offline with only scikit-learn, needs no
  model downloads, and captures a useful notion of semantic similarity
  for short technical product descriptions (it's robust to word order and
  partial token overlap, e.g. "cordless drill 18v" vs "18v cordless drill").

- `SentenceTransformerEmbeddingBackend` (optional): true neural sentence
  embeddings via the `sentence-transformers` package, used automatically if
  installed and requested. Because both backends implement the same
  `.fit()` / `.encode()` interface, `semantic_matcher.py` never needs to
  know which one is active — swapping backends is a one-line change in
  `EmbeddingService(backend=...)`.

The service always fits on the *combined* corpus (all catalog items) so
that the vector space is shared between catalog and query embeddings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np


class EmbeddingBackend(Protocol):
    def fit(self, corpus: list[str]) -> None: ...

    def encode(self, texts: list[str]) -> np.ndarray: ...


@dataclass
class TfidfEmbeddingBackend:
    """Offline embedding backend: word + char n-gram TF-IDF -> SVD.

    n_components is capped automatically to the corpus size so this also
    works on tiny test fixtures.
    """

    n_components: int = 128
    random_state: int = 42

    def __post_init__(self) -> None:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.decomposition import TruncatedSVD
        from sklearn.pipeline import FeatureUnion

        self._word_vectorizer = TfidfVectorizer(
            analyzer="word", ngram_range=(1, 2), min_df=1
        )
        self._char_vectorizer = TfidfVectorizer(
            analyzer="char_wb", ngram_range=(3, 5), min_df=1
        )
        self._union = FeatureUnion(
            [
                ("word", self._word_vectorizer),
                ("char", self._char_vectorizer),
            ]
        )
        self._svd = None
        self._fitted = False

    def fit(self, corpus: list[str]) -> None:
        from sklearn.decomposition import TruncatedSVD

        corpus = [c if c else " " for c in corpus]
        sparse = self._union.fit_transform(corpus)
        n_components = min(self.n_components, max(2, min(sparse.shape) - 1))
        self._svd = TruncatedSVD(
            n_components=n_components, random_state=self.random_state
        )
        self._svd.fit(sparse)
        self._fitted = True

    def encode(self, texts: list[str]) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError("TfidfEmbeddingBackend.fit() must be called before encode()")
        texts = [t if t else " " for t in texts]
        sparse = self._union.transform(texts)
        dense = self._svd.transform(sparse)
        norms = np.linalg.norm(dense, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return dense / norms


@dataclass
class SentenceTransformerEmbeddingBackend:
    """Optional neural embedding backend. Requires `sentence-transformers`.

    Kept isolated behind a lazy import so the rest of the engine works even
    when the (large) torch/sentence-transformers dependency isn't installed.
    """

    model_name: str = "all-MiniLM-L6-v2"

    def __post_init__(self) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(self.model_name)

    def fit(self, corpus: list[str]) -> None:
        # Neural sentence embedding models are pre-trained; no fitting step.
        return None

    def encode(self, texts: list[str]) -> np.ndarray:
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return np.asarray(vectors)


class EmbeddingService:
    """Thin façade the rest of the engine talks to.

    Usage:
        service = EmbeddingService()              # offline TF-IDF/SVD backend
        service = EmbeddingService(backend=SentenceTransformerEmbeddingBackend())

        service.fit(all_catalog_texts)
        catalog_vecs = service.encode(catalog_texts)
        query_vecs = service.encode(query_texts)
    """

    def __init__(self, backend: EmbeddingBackend | None = None):
        self.backend: EmbeddingBackend = backend or TfidfEmbeddingBackend()

    def fit(self, corpus: list[str]) -> None:
        self.backend.fit(corpus)

    def encode(self, texts: list[str]) -> np.ndarray:
        return self.backend.encode(texts)
