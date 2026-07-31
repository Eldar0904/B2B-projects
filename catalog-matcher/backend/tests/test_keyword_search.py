from app.services.search.keyword_search import KeywordIndex
from app.services.search.types import MasterProductRecord


def _records():
    return [
        MasterProductRecord(id="1", external_id="A", normalized_name="стол детский регулируемый 1200x600"),
        MasterProductRecord(id="2", external_id="B", normalized_name="кресло офисное кожаное"),
        MasterProductRecord(id="3", external_id="C", normalized_name="стол ученический регулируемый"),
        MasterProductRecord(id="4", external_id="G", normalized_name="группа мебель", is_group_header=True),
    ]


def test_exact_keyword_overlap_ranks_first():
    index = KeywordIndex(_records())
    results = index.search("стол детский регулируемый", top_k=5)
    assert results[0][0] == "1"


def test_scores_are_absolute_not_relative_to_best_hit():
    """The top hit must NOT be auto-normalized to 1.0.

    This asserts the fix for the bug where every query's best result was
    divided by itself and therefore always scored 1.0, regardless of how
    poor it was. On the real files that made "Оборудование для единоборств"
    score 0.93 against "Обучающие плакаты для дошкольников", because the
    only shared token was the stopword "для".

    Scores must instead be comparable ACROSS queries, so a weak best-hit
    stays low.
    """
    index = KeywordIndex(_records())

    strong = index.search("стол детский регулируемый", top_k=5)
    assert strong[0][1] < 1.0, "top hit must not be renormalized to a perfect score"
    assert 0.0 < strong[0][1] <= 1.0

    # A query sharing only a single weak token must score well below a
    # query that matches nearly the whole name.
    weak = index.search("кресло", top_k=5)
    assert weak, "expected the single shared token to still retrieve something"
    assert weak[0][1] < strong[0][1]


def test_stopwords_do_not_create_matches():
    """"для" alone must not retrieve anything - it carries no signal."""
    index = KeywordIndex(
        [MasterProductRecord(id="1", external_id="A", normalized_name="оборудование для единоборств")]
    )
    assert index.search("для", top_k=5) == []


def test_group_headers_are_excluded():
    index = KeywordIndex(_records())
    results = index.search("группа мебель", top_k=5)
    assert all(r[0] != "4" for r in results)


def test_empty_query_returns_no_results():
    index = KeywordIndex(_records())
    assert index.search("", top_k=5) == []


def test_empty_catalog_does_not_crash():
    index = KeywordIndex([])
    assert index.search("стол", top_k=5) == []
