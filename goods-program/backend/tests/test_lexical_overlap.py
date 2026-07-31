"""Tests for the IDF-weighted coverage signal.

These encode the specific real-world failures the signal was added to
prevent - see search/lexical_overlap.py for the measured numbers.
"""

from app.services.search.lexical_overlap import LexicalOverlapIndex
from app.services.search.types import MasterProductRecord


def _records():
    return [
        MasterProductRecord(id="1", external_id="A", normalized_name="шкаф стеллаж открытый 849x360x1835мм"),
        MasterProductRecord(id="2", external_id="B", normalized_name="оборудование для единоборств"),
        MasterProductRecord(id="3", external_id="C", normalized_name="ларь для овощей"),
        MasterProductRecord(id="4", external_id="D", normalized_name="стол рабочий на 4 детей"),
        MasterProductRecord(id="G", external_id="G", normalized_name="группа мебель", is_group_header=True),
    ]


def test_full_coverage_when_every_query_term_present():
    index = LexicalOverlapIndex(_records())
    # Both content words appear in candidate 1, despite its extra spec text.
    assert index.score("стеллаж открытый", "1") == 1.0


def test_zero_coverage_for_unrelated_candidate():
    """The exact false positive that motivated this module.

    "обучающие плакаты дошкольников" shares only the stopword "для" with
    "оборудование для единоборств". Under the old scorer that pair reached
    0.479 and was shown as the top suggestion; coverage must be 0.
    """
    index = LexicalOverlapIndex(_records())
    assert index.score("обучающие плакаты для дошкольников", "2") == 0.0


def test_stopwords_alone_never_produce_coverage():
    index = LexicalOverlapIndex(_records())
    # "для" is shared with candidates 2 and 3 but is a stopword.
    assert index.score("для", "2") == 0.0
    assert index.score("для", "3") == 0.0


def test_partial_coverage_is_between_zero_and_one():
    index = LexicalOverlapIndex(_records())
    score = index.score("стол детский", "4")
    assert 0.0 < score < 1.0


def test_rare_terms_dominate_common_ones():
    """A query's distinctive word must matter more than its common one.

    "стол" appears in the catalog; "дарсонвализации" does not. Missing the
    rare word should cost far more than missing the common one.
    """
    records = _records() + [
        MasterProductRecord(id="5", external_id="E", normalized_name="стол лабораторный"),
    ]
    index = LexicalOverlapIndex(records)
    # Candidate 5 covers "стол" but not the distinctive term.
    partial = index.score("стол дарсонвализации", "5")
    assert partial < 0.5, "covering only the common word must not look like a half-match"


def test_group_headers_are_not_scoreable():
    index = LexicalOverlapIndex(_records())
    assert index.score("группа мебель", "G") == 0.0


def test_unknown_candidate_id_returns_zero():
    index = LexicalOverlapIndex(_records())
    assert index.score("стол", "does-not-exist") == 0.0


def test_empty_query_returns_zero():
    index = LexicalOverlapIndex(_records())
    assert index.score("", "1") == 0.0
    assert index.score("   ", "1") == 0.0


def test_empty_catalog_does_not_crash():
    index = LexicalOverlapIndex([])
    assert index.score("стол", "1") == 0.0
