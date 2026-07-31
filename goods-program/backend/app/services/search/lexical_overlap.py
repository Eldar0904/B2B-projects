"""IDF-weighted query coverage ("did the catalog entry actually account
for the words the user asked for?").

This signal is new in v2 and, measured on the real files, it is the single
strongest discriminator between a true match and a plausible-looking
false one.

--- Why the other three signals were not enough --------------------------

Each of the existing retrieval signals can be fooled in a specific way:

- BM25 rewards *any* shared token, and long catalog entries have many
  tokens to share.
- `token_set_ratio` measures containment, so a 2-word query is trivially
  "100% contained" in some 15-word catalog name.
- Character TF-IDF measures surface similarity, so "Демонстрационные
  картинки" looks similar to "Динамометр демонстрационный".

All three answer "is there overlap?". None answers "is the *important*
part of the query accounted for?" - and that is the question that
separates a real match from a coincidence.

--- What this computes ---------------------------------------------------

For query Q and candidate C:

    coverage(Q, C) = sum(idf(w) for w in tokens(Q) & tokens(C))
                     ---------------------------------------------
                     sum(idf(w) for w in tokens(Q))

i.e. the fraction of the query's *information content* (not its word
count) that the candidate actually contains. IDF is computed over the
master catalog, so common catalog words like "стол" contribute little
while distinctive ones like "дарсонвализации" dominate.

Worked example from the real data:

    Q = "стеллаж открытый"
        -> "шкаф стеллаж открытый 849х360х1835мм" covers both content
           words: coverage = 1.00                          (true match)

    Q = "обучающие плакаты дошкольников"
        -> "оборудование единоборств" covers none of them:
           coverage = 0.00                                (correctly rejected)

Under the old scorer that second pair scored 0.479 - high enough to be
shown as the top suggestion. With coverage weighted in, it scores 0.224.

Unknown tokens (present in the query, absent from the whole catalog) get a
neutral-high IDF via `_default_idf`, so a query full of words the catalog
has never seen - the Kazakh book titles in this dataset, for instance -
scores LOW rather than accidentally high. That is the desired behaviour:
those products genuinely are not in the catalog and should be surfaced as
"no match" rather than matched to something arbitrary.
"""

from __future__ import annotations

import math
from collections import Counter

from app.services.normalizer import tokenize
from app.services.search.types import MasterProductRecord


class LexicalOverlapIndex:
    """Not a retrieval index - it does not produce candidates on its own.

    It only *scores* a (query, candidate) pair, so it is applied to the
    candidate pool gathered by BM25 / fuzzy / vector search. Exposing it
    as a `.score()` rather than a `.search()` keeps `hybrid_search.py`'s
    retrieval loop honest: this signal can veto a bad candidate, but it is
    never the reason a candidate was retrieved in the first place.
    """

    def __init__(self, records: list[MasterProductRecord]):
        indexable = [r for r in records if not r.is_group_header]
        self._tokens_by_id: dict[str, frozenset[str]] = {
            r.id: frozenset(tokenize(r.search_text())) for r in indexable
        }

        document_frequency: Counter[str] = Counter()
        for tokens in self._tokens_by_id.values():
            document_frequency.update(tokens)

        total_docs = max(len(self._tokens_by_id), 1)
        self._idf: dict[str, float] = {
            token: math.log(1.0 + total_docs / (1.0 + df))
            for token, df in document_frequency.items()
        }
        # A token the catalog has never seen is maximally distinctive.
        self._default_idf = math.log(1.0 + total_docs)

    def idf(self, token: str) -> float:
        return self._idf.get(token, self._default_idf)

    def score(self, query: str, master_product_id: str) -> float:
        """Fraction of the query's IDF mass present in the candidate."""
        candidate_tokens = self._tokens_by_id.get(master_product_id)
        if not candidate_tokens:
            return 0.0

        query_tokens = set(tokenize(query))
        if not query_tokens:
            return 0.0

        total = sum(self.idf(t) for t in query_tokens)
        if total <= 0:
            return 0.0
        covered = sum(self.idf(t) for t in query_tokens & candidate_tokens)
        return covered / total
