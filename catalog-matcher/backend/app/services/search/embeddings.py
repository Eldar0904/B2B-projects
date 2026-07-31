"""Embedding provider abstraction.

Two implementations behind one interface, selected via
`settings.embedding_provider`:

- `SentenceTransformerEmbeddingProvider` (**now the default**): a real
  multilingual model, so Russian, Kazakh and English product text embed
  into one comparable space. Requires
  `backend/requirements-embeddings.txt` and network access to download
  model weights on first run (cached afterwards, then fully offline).
- `TfidfEmbeddingProvider`: a lexical fallback for when the model cannot
  be downloaded. Not a semantic model - see the warning below.

--- Why the old TF-IDF default was actively harmful ---------------------

The previous default was `TfidfVectorizer(analyzer="char_wb",
ngram_range=(2, 4), max_features=256)`. 256 character n-grams is far too
small a basis for a 5,000-product Cyrillic catalog: almost every product
projects onto almost the same handful of common Cyrillic bigrams, so
*everything* ends up looking similar to everything.

That is not a subtle degradation. Measured on the real files, the mean
cosine similarity of the best hit was 0.692 with 256 features and 0.447
with 20,000 - the *higher* number was the broken one, because the space
had collapsed and could no longer tell products apart. Concretely it
produced nonsense like:

    "Ертегілер. Өзіміз оқимыз"  ~  "Тележка грузовая М без сумки"   0.34
    "Динозаврлар. энциклопедия" ~  "Датчик давления"                0.39

Two fixes here. First, `max_features` is no longer capped at a degenerate
value. Second, the fallback now combines a WORD-level and a CHARACTER-level
TF-IDF: word n-grams carry the actual product terminology, character
n-grams absorb typos and morphological endings (Russian and Kazakh are
both heavily inflected, so "стола"/"столы"/"столов" must not be three
unrelated features). Neither alone was sufficient.

Even so, TF-IDF is a *lexical* method wearing an embedding interface. It
cannot know that "балансир" and "баланс құралы" mean the same thing.
That is precisely what the user needs for the Kazakh rows in this dataset,
which is why the real model is now the default and this is the fallback.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import numpy as np

logger = logging.getLogger(__name__)

# Below this many documents, TruncatedSVD degrades ranking instead of
# helping (see TfidfEmbeddingProvider.fit). Real catalogs are far above it;
# unit-test fixtures are far below.
_MIN_DOCS_FOR_SVD = 100


class EmbeddingProvider(ABC):
    @abstractmethod
    def fit(self, texts: list[str]) -> None:
        """Build any corpus-dependent state (e.g. TF-IDF vocabulary)."""

    @abstractmethod
    def embed(self, texts: list[str]) -> np.ndarray:
        """Return an (n, dim) array of embeddings for the given texts."""

    @property
    @abstractmethod
    def dim(self) -> int:
        ...


class TfidfEmbeddingProvider(EmbeddingProvider):
    """Lexical fallback. See the module docstring for why the previous
    256-feature configuration was replaced.
    """

    def __init__(self, max_features: int = 60000, char_weight: float = 0.6, n_components: int = 384):
        from sklearn.feature_extraction.text import TfidfVectorizer

        # Word-level: real product terminology, incl. bigrams like
        # "стеллаж открытый" that a unigram model would split.
        self._word_vectorizer = TfidfVectorizer(
            analyzer="word",
            ngram_range=(1, 2),
            sublinear_tf=True,
            min_df=1,
        )
        # Character-level: typo and inflection tolerance. char_wb keeps
        # n-grams inside word boundaries so they stay meaningful.
        self._char_vectorizer = TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=(3, 5),
            sublinear_tf=True,
            min_df=2,
            max_features=max_features,
        )
        self._char_weight = char_weight
        # Qdrant stores DENSE vectors, so the combined TF-IDF space (~70k
        # sparse dimensions on the real catalog) cannot be handed to it
        # directly - 5,163 products x 70k float32 would be ~1.4 GB.
        # TruncatedSVD (i.e. LSA) projects it down to a dense space of
        # `n_components`. This is not merely a size workaround: LSA also
        # merges correlated terms, which recovers a little of the semantic
        # behaviour a real embedding model would give. 384 matches the
        # dimensionality of common sentence-transformer models.
        #
        # This is where the OLD code went wrong: it reached the same dense
        # size by truncating the *vocabulary* to 256 char n-grams, which
        # destroys the signal, instead of projecting the full vocabulary.
        self._n_components = n_components
        self._svd = None
        self._fitted = False
        self._dim = 0

    def _raw_matrix(self, texts: list[str], fit: bool):
        from scipy.sparse import hstack
        from sklearn.preprocessing import normalize

        if fit:
            word_matrix = self._word_vectorizer.fit_transform(texts)
            try:
                char_matrix = self._char_vectorizer.fit_transform(texts)
            except ValueError:
                # min_df=2 can empty the vocabulary on a tiny corpus (e.g.
                # unit tests with 3 products). Retry at min_df=1.
                self._char_vectorizer.set_params(min_df=1)
                char_matrix = self._char_vectorizer.fit_transform(texts)
        else:
            word_matrix = self._word_vectorizer.transform(texts)
            char_matrix = self._char_vectorizer.transform(texts)
        return normalize(hstack([word_matrix, char_matrix * self._char_weight]).tocsr())

    def fit(self, texts: list[str]) -> None:
        from sklearn.decomposition import TruncatedSVD
        from sklearn.preprocessing import normalize

        non_empty = [t for t in texts if t and t.strip()]
        if not non_empty:
            # Fit on a placeholder with real character content so the
            # vocabulary is never empty (an all-blank corpus would raise).
            non_empty = ["placeholder text"]

        matrix = self._raw_matrix(non_empty, fit=True)
        n_docs, n_features = matrix.shape

        # Only project when there is enough data for the projection to be
        # meaningful AND the raw space is actually too large to store.
        #
        # On a tiny corpus, SVD is not a compression - it is destruction.
        # With 3 documents the projection collapses to 2 components and the
        # ranking becomes essentially arbitrary (this was caught by
        # test_vector_search: the query "стол детский регулируемый" ranked
        # "стол ученический регулируемый" above the exact match). Below the
        # threshold the exact sparse cosine is both more accurate and small
        # enough to store densely, so use it directly.
        should_project = n_docs >= _MIN_DOCS_FOR_SVD and n_features > self._n_components
        n_components = min(self._n_components, min(n_docs, n_features) - 1)

        if should_project and n_components >= 2:
            self._svd = TruncatedSVD(n_components=n_components, random_state=0)
            self._svd.fit(matrix)
            self._dim = n_components
        else:
            self._svd = None
            self._dim = n_features
        self._fitted = True

    def embed(self, texts: list[str]) -> np.ndarray:
        from sklearn.preprocessing import normalize

        if not self._fitted:
            self.fit(texts)
        matrix = self._raw_matrix(texts, fit=False)
        if self._svd is not None:
            dense = self._svd.transform(matrix)
        else:
            dense = np.asarray(matrix.todense())
        # Re-normalize after projection so cosine similarity is a dot product.
        return normalize(dense).astype(np.float32)

    @property
    def dim(self) -> int:
        return self._dim


class SentenceTransformerEmbeddingProvider(EmbeddingProvider):
    """Real multilingual embedding model. Lazily imports
    `sentence_transformers` so the base install doesn't require torch.

    Default model is LaBSE, chosen over paraphrase-multilingual-mpnet
    because LaBSE explicitly covers Kazakh (kk) in its 109 languages and
    is trained for cross-lingual sentence alignment - i.e. it puts a
    Kazakh phrase and its Russian equivalent near each other in the same
    space, which is the actual requirement here. The mpnet paraphrase
    model's Kazakh coverage is far weaker.
    """

    def __init__(self, model_name: str = "sentence-transformers/LaBSE"):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - only when misconfigured
            raise ImportError(
                "sentence-transformers is not installed. Run: "
                "pip install -r requirements-embeddings.txt, then set "
                "EMBEDDING_PROVIDER=sentence-transformers."
            ) from exc
        self._model = SentenceTransformer(model_name)

    def fit(self, texts: list[str]) -> None:
        # Sentence-transformer models are pretrained; nothing to fit.
        pass

    def embed(self, texts: list[str]) -> np.ndarray:
        # normalize_embeddings=True so downstream cosine similarity is a
        # plain dot product and scores are directly comparable.
        return np.asarray(
            self._model.encode(
                texts,
                show_progress_bar=False,
                normalize_embeddings=True,
                batch_size=64,
            ),
            dtype=np.float32,
        )

    @property
    def dim(self) -> int:
        return self._model.get_sentence_embedding_dimension()


def build_embedding_provider(provider_name: str, model_name: str) -> EmbeddingProvider:
    """Build the configured provider.

    If the real model is requested but unavailable (no network on first
    run, or the optional dependency is missing), fall back to TF-IDF with
    a loud warning rather than failing the whole index build. A degraded
    match is recoverable; a backend that won't start is not.
    """
    if provider_name == "tfidf":
        return TfidfEmbeddingProvider()
    if provider_name == "sentence-transformers":
        try:
            return SentenceTransformerEmbeddingProvider(model_name)
        except Exception as exc:  # noqa: BLE001 - any load/download failure
            logger.warning(
                "Could not load sentence-transformers model %r (%s). "
                "Falling back to TF-IDF embeddings. Kazakh/Russian semantic "
                "matching will be significantly weaker until this is fixed - "
                "check network access on first run, then restart.",
                model_name,
                exc,
            )
            return TfidfEmbeddingProvider()
    raise ValueError(f"Unknown embedding_provider: {provider_name}")
