from app.services.search.fuzzy_search import FuzzyIndex
from app.services.search.types import MasterProductRecord


def _records():
    return [
        MasterProductRecord(id="1", external_id="A", normalized_name="стол детский регулируемый"),
        MasterProductRecord(id="2", external_id="B", normalized_name="кресло офисное"),
        MasterProductRecord(id="3", external_id="G", normalized_name="группа", is_group_header=True),
    ]


def test_typo_still_matches_closely():
    index = FuzzyIndex(_records())
    results = index.search("столь детский регулируемый", top_k=5)
    assert results[0][0] == "1"
    assert results[0][1] > 0.9


def test_word_order_does_not_matter():
    index = FuzzyIndex(_records())
    results = index.search("регулируемый детский стол", top_k=5)
    assert results[0][0] == "1"
    assert results[0][1] == 1.0  # token_sort_ratio is order-independent


def test_group_headers_excluded():
    index = FuzzyIndex(_records())
    results = index.search("группа", top_k=5)
    assert all(r[0] != "3" for r in results)


def test_empty_query_returns_no_results():
    index = FuzzyIndex(_records())
    assert index.search("   ", top_k=5) == []
