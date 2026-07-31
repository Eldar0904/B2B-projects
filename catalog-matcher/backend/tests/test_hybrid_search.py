from app.services.search.hybrid_search import retrieve_candidates
from app.services.search.scoring import ScoringWeights, compute_final_score


class _FakeIndex:
    def __init__(self, hits: dict[str, float]):
        self._hits = hits

    def search(self, query: str, top_k: int):
        return sorted(self._hits.items(), key=lambda kv: kv[1], reverse=True)[:top_k]


def test_combines_and_scores_using_spec_weights():
    keyword = _FakeIndex({"1": 1.0, "2": 0.2})
    fuzzy = _FakeIndex({"1": 0.9})
    vector = _FakeIndex({"1": 0.95, "3": 0.4})

    results = retrieve_candidates(
        "стол детский регулируемый",
        keyword_index=keyword,
        fuzzy_index=fuzzy,
        vector_index=vector,
        candidates_per_method=20,
        top_k=20,
    )

    by_id = {c.master_product_id: c for c in results}
    assert set(by_id) == {"1", "2", "3"}

    expected_1 = compute_final_score(embedding_score=0.95, keyword_score=1.0, fuzzy_name_score=0.9)
    assert abs(by_id["1"].final_score - expected_1) < 1e-9
    assert by_id["1"].matched_by == {"keyword", "fuzzy", "vector"}

    # Candidate found by only one method still appears, with the other
    # sub-scores at 0 rather than crashing or being silently dropped.
    assert by_id["3"].keyword_score == 0.0
    assert by_id["3"].fuzzy_name_score == 0.0
    assert by_id["3"].matched_by == {"vector"}

    # Ranking follows final_score descending.
    assert results[0].master_product_id == "1"


def test_respects_top_k_limit():
    keyword = _FakeIndex({str(i): 1.0 / (i + 1) for i in range(30)})
    fuzzy = _FakeIndex({})
    vector = _FakeIndex({})

    results = retrieve_candidates(
        "query",
        keyword_index=keyword,
        fuzzy_index=fuzzy,
        vector_index=vector,
        candidates_per_method=20,
        top_k=5,
    )
    assert len(results) == 5


def test_custom_weights_change_ranking():
    keyword = _FakeIndex({"a": 1.0, "b": 0.0})
    fuzzy = _FakeIndex({"a": 0.0, "b": 0.0})
    vector = _FakeIndex({"a": 0.0, "b": 1.0})

    keyword_favoring = ScoringWeights(
        embedding_score=0.0, keyword_score=1.0, fuzzy_name_score=0.0,
        lexical_overlap_score=0.0,
    )
    vector_favoring = ScoringWeights(
        embedding_score=1.0, keyword_score=0.0, fuzzy_name_score=0.0,
        lexical_overlap_score=0.0,
    )

    r1 = retrieve_candidates(
        "q", keyword_index=keyword, fuzzy_index=fuzzy, vector_index=vector, weights=keyword_favoring
    )
    r2 = retrieve_candidates(
        "q", keyword_index=keyword, fuzzy_index=fuzzy, vector_index=vector, weights=vector_favoring
    )
    assert r1[0].master_product_id == "a"
    assert r2[0].master_product_id == "b"
