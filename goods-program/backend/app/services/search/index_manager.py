"""Ties the three retrieval methods together into one buildable/queryable
index over the master catalog, plus a process-wide singleton so the API
layer doesn't rebuild everything on every request.

Rebuilding is intentionally an explicit action (`build()` /
`POST /api/search/reindex`), not automatic on every DB write — indexing a
large catalog isn't free, and spec section 30 expects a visible
"processing" step with progress, not a silent per-request cost.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.services.search.embeddings import build_embedding_provider
from app.services.search.fuzzy_search import FuzzyIndex
from app.services.search.hybrid_search import retrieve_candidates
from app.services.search.keyword_search import KeywordIndex
from app.services.search.lexical_overlap import LexicalOverlapIndex
from app.services.search.reranking import Reranker, build_reranker
from app.services.search.scoring import DEFAULT_WEIGHTS, ScoringWeights
from app.services.search.types import MasterProductRecord, ScoredCandidate
from app.services.search.vector_search import VectorIndex, build_qdrant_client


@dataclass
class IndexStats:
    total_records: int
    indexed_records: int  # excludes group headers
    group_headers_excluded: int
    embedding_dim: int


class CatalogSearchIndex:
    def __init__(self, weights: ScoringWeights = DEFAULT_WEIGHTS, reranker: Reranker | None = None):
        self._weights = weights
        self._keyword_index: KeywordIndex | None = None
        self._fuzzy_index: FuzzyIndex | None = None
        self._vector_index: VectorIndex | None = None
        self._overlap_index: LexicalOverlapIndex | None = None
        self._stats: IndexStats | None = None
        self._candidate_texts: dict[str, str] = {}
        self._reranker = reranker  # None -> built from settings in build(), unless injected (e.g. tests)

    @property
    def is_built(self) -> bool:
        return self._stats is not None

    @property
    def stats(self) -> IndexStats | None:
        return self._stats

    def build(self, records: list[MasterProductRecord]) -> IndexStats:
        indexable = [r for r in records if not r.is_group_header]

        self._keyword_index = KeywordIndex(records)
        self._fuzzy_index = FuzzyIndex(records)
        self._overlap_index = LexicalOverlapIndex(records)
        self._candidate_texts = {r.id: r.search_text() for r in indexable}

        if self._reranker is None:
            # See build_reranker's own docstring: this also wires in the
            # LLM tie-breaker (HANDOFF.md section 7) when configured -
            # off and inert by default (enable_llm_reranker_for_hard_cases
            # is False), same as every other optional provider here.
            self._reranker = build_reranker(
                settings.reranker_provider,
                settings.cross_encoder_model_name,
                enable_llm_tiebreaker=settings.enable_llm_reranker_for_hard_cases,
                llm_reranker_provider=settings.llm_reranker_provider,
                anthropic_api_key=settings.anthropic_api_key,
                anthropic_model=settings.llm_reranker_model,
                gemini_api_key=settings.gemini_api_key,
                gemini_model=settings.gemini_reranker_model,
                llm_ambiguity_threshold=settings.llm_ambiguity_threshold,
            )

        embedding_provider = build_embedding_provider(
            settings.embedding_provider, settings.embedding_model_name
        )
        client = build_qdrant_client(settings.qdrant_url, settings.qdrant_local_path)
        self._vector_index = VectorIndex(client, embedding_provider, settings.qdrant_collection_name)
        self._vector_index.build(records)

        self._stats = IndexStats(
            total_records=len(records),
            indexed_records=len(indexable),
            group_headers_excluded=len(records) - len(indexable),
            embedding_dim=embedding_provider.dim,
        )
        return self._stats

    def search(self, query_text: str, top_k: int | None = None) -> list[ScoredCandidate]:
        """Raw Phase 2 hybrid-search results, ordered by `final_score`.
        Used by Phase 5 auto-matching and the raw `/api/search/candidates`
        endpoint - deliberately NOT reranked, so auto-accept thresholds
        keep meaning exactly what ARCHITECTURE.md documents.
        """
        if not self.is_built:
            raise RuntimeError("Index has not been built yet. Call build() or POST /api/search/reindex first.")
        return retrieve_candidates(
            query_text,
            keyword_index=self._keyword_index,
            fuzzy_index=self._fuzzy_index,
            vector_index=self._vector_index,
            overlap_index=self._overlap_index,
            candidates_per_method=settings.candidates_per_method,
            top_k=top_k or settings.top_k_candidates,
            weights=self._weights,
        )

    def search_reranked(self, query_text: str, top_k: int = 3, pool_size: int | None = None) -> list[ScoredCandidate]:
        """Phase 6: retrieve the top-N pool (default `RERANK_POOL_SIZE`,
        spec section 13's "top 20"), rerank it, and return the top `top_k`
        by `reranker_score`. This is what Phase 3's human-review screen
        uses (via `matching.get_top_candidates`).
        """
        if not self.is_built:
            raise RuntimeError("Index has not been built yet. Call build() or POST /api/search/reindex first.")
        pool = self.search(query_text, top_k=pool_size or settings.rerank_pool_size)
        reranked = self._reranker.rerank(query_text, pool, self._candidate_texts)
        return reranked[:top_k]


# Process-wide singleton. Fine for a single-process MVP; if this moves to
# multiple worker processes, indexing should move to a shared service
# (Qdrant already supports that) with keyword/fuzzy indexes rebuilt per
# worker on startup.
_singleton: CatalogSearchIndex | None = None


def get_index() -> CatalogSearchIndex:
    global _singleton
    if _singleton is None:
        _singleton = CatalogSearchIndex()
    return _singleton


# --- Per-catalog-version cache (HANDOFF.md section 4, Task 1) --------------
#
# `get_index()` above is the single global index the main app's
# `/api/search/reindex` and startup auto-rebuild use - one catalog, always
# rebuilt in full. The standalone wizard is different: it can have several
# CatalogVersions on file (an April catalog, a May catalog...) and must not
# rebuild BM25/fuzzy/vectors from scratch every time someone reuses one
# that's already indexed. This is a *separate* cache, keyed by
# catalog_version_id, deliberately not folded into the same singleton -
# folding them together would mean "reuse an existing catalog" in the
# wizard could silently evict or be evicted by the main app's global index.
#
# Same single-process caveat as `_singleton`: this lives in memory only,
# so it is empty again after a restart. `standalone_matching.run_matching_job`
# already handles that (rebuilds and re-caches on a miss) - see its
# docstring.
_version_index_cache: dict[str, CatalogSearchIndex] = {}


def get_cached_index_for_version(catalog_version_id: str) -> CatalogSearchIndex | None:
    return _version_index_cache.get(catalog_version_id)


def cache_index_for_version(catalog_version_id: str, index: CatalogSearchIndex) -> None:
    _version_index_cache[catalog_version_id] = index


def invalidate_cached_index_for_version(catalog_version_id: str) -> None:
    """No caller yet, but a cleanup path (e.g. deleting a CatalogVersion)
    must not leave a stale index reachable by an id that no longer exists
    in the database - added now so that isn't a later surprise.
    """
    _version_index_cache.pop(catalog_version_id, None)
