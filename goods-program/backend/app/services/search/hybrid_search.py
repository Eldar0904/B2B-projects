"""Hybrid search orchestration: combine BM25 / fuzzy / vector candidates
into a single ranked top-K list, then score each with the IDF-weighted
coverage signal.

This module is retrieval-method-agnostic - it takes already-built indexes
(any objects with a `.search(query, top_k) -> list[(id, score)]` method)
so it can be unit tested with tiny fake indexes, and so swapping any one
retrieval method's implementation never requires touching this file.

Note the asymmetry between the three retrieval indexes and the overlap
index: BM25/fuzzy/vector *propose* candidates, whereas
`LexicalOverlapIndex` only *scores* candidates that were already proposed.
That is deliberate - see `lexical_overlap.py`. It means a candidate can
never be retrieved on coverage alone, but a retrieved candidate with poor
coverage is correctly pushed down.
"""

from __future__ import annotations

from typing import Protocol

from app.services.search.scoring import DEFAULT_WEIGHTS, ScoringWeights, compute_final_score
from app.services.search.types import ScoredCandidate


class SearchIndex(Protocol):
    def search(self, query: str, top_k: int) -> list[tuple[str, float]]: ...


class OverlapIndex(Protocol):
    def score(self, query: str, master_product_id: str) -> float: ...


def retrieve_candidates(
    query_text: str,
    *,
    keyword_index: SearchIndex,
    fuzzy_index: SearchIndex,
    vector_index: SearchIndex,
    overlap_index: OverlapIndex | None = None,
    candidates_per_method: int = 20,
    top_k: int = 20,
    weights: ScoringWeights = DEFAULT_WEIGHTS,
) -> list[ScoredCandidate]:
    """Retrieve, combine, score, and rank candidates for one destination
    product's (already normalized) search text.

    Retrieves ~`candidates_per_method` from each method, combines and
    dedupes, then computes the weighted score. Reranking (cross-encoder /
    LLM) happens afterwards in `index_manager.search_reranked`.

    `overlap_index` is optional so existing unit tests that construct this
    with three fake indexes keep working; when it is None the coverage
    signal contributes 0.0 and the remaining weights still apply.
    """
    keyword_hits = dict(keyword_index.search(query_text, candidates_per_method))
    fuzzy_hits = dict(fuzzy_index.search(query_text, candidates_per_method))
    vector_hits = dict(vector_index.search(query_text, candidates_per_method))

    all_ids = set(keyword_hits) | set(fuzzy_hits) | set(vector_hits)

    candidates: list[ScoredCandidate] = []
    for master_product_id in all_ids:
        keyword_score = keyword_hits.get(master_product_id, 0.0)
        fuzzy_score = fuzzy_hits.get(master_product_id, 0.0)
        embedding_score = vector_hits.get(master_product_id, 0.0)
        overlap_score = (
            overlap_index.score(query_text, master_product_id) if overlap_index else 0.0
        )

        matched_by = set()
        if master_product_id in keyword_hits:
            matched_by.add("keyword")
        if master_product_id in fuzzy_hits:
            matched_by.add("fuzzy")
        if master_product_id in vector_hits:
            matched_by.add("vector")

        final_score = compute_final_score(
            embedding_score=embedding_score,
            keyword_score=keyword_score,
            fuzzy_name_score=fuzzy_score,
            lexical_overlap_score=overlap_score,
            weights=weights,
        )

        candidates.append(
            ScoredCandidate(
                master_product_id=master_product_id,
                embedding_score=embedding_score,
                keyword_score=keyword_score,
                fuzzy_name_score=fuzzy_score,
                lexical_overlap_score=overlap_score,
                final_score=final_score,
                matched_by=matched_by,
            )
        )

    candidates.sort(key=lambda c: c.final_score, reverse=True)
    return candidates[:top_k]
