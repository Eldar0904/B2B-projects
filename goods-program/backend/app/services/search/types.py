"""Shared data types for the Phase 2 search pipeline.

Plain dataclasses (not SQLAlchemy models) so the retrieval/scoring code can
be unit tested without a database, and so it doesn't care whether a record
came from Postgres, a CSV, or a test fixture.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MasterProductRecord:
    """The subset of a MasterProduct row that search needs."""

    id: str
    external_id: str | None
    normalized_name: str
    description: str | None = None
    is_group_header: bool = False

    def search_text(self) -> str:
        """Text representation used for keyword/embedding indexing.

        Deliberately name-only, not name+description. Destination products
        are always queried by name alone (see matching.get_top_candidates
        and matching.try_auto_match, which both build the query text from
        normalized_name only) - so indexing the master catalog's text with
        description folded in created an asymmetry: two products with an
        identical name but different descriptions ended up with diluted
        similarity, since the master side's vector/BM25 representation
        carried extra description tokens the query side never had.
        Real example that surfaced this: "Грелка резиновая" (destination)
        vs. the identically-named master product only scored 79% embedding
        similarity instead of ~100%, purely because of the master
        product's description. Keeping both sides name-only fixes that.
        """
        return self.normalized_name


@dataclass
class ScoredCandidate:
    """One master product candidate for a destination product, with the
    individual sub-scores that fed into `final_score` (spec section 18:
    the UI should be able to explain why a candidate was selected using
    real features, not fake explanations).
    """

    master_product_id: str
    embedding_score: float = 0.0
    keyword_score: float = 0.0
    fuzzy_name_score: float = 0.0
    # v2: IDF-weighted fraction of the query's information content that the
    # candidate actually contains. See search/lexical_overlap.py - this is
    # the signal that distinguishes a real match from a coincidental one.
    lexical_overlap_score: float = 0.0
    final_score: float = 0.0
    matched_by: set[str] = field(default_factory=set)  # e.g. {"keyword", "vector"}

    # Phase 6: populated by a Reranker after retrieval, when reranking is
    # enabled. 0.0 and unused otherwise. Reordering the top-20 pool by this
    # (rather than final_score) is what determines the top-3 shown to the
    # user - see reranking.py.
    reranker_score: float = 0.0
