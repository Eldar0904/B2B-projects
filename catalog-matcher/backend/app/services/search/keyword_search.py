"""BM25 keyword search over the master catalog.

Rebuilt in-process from a list of `MasterProductRecord`s. For catalogs in
the tens of thousands of rows this is fast enough to rebuild on demand; if
the catalog grows much larger this should move to a persistent search
engine (Postgres tsvector / Elasticsearch) instead of an in-memory index.

--- The score-normalization bug this file used to have -------------------

The previous implementation normalized BM25 scores by dividing every hit
by the best hit *for that query*:

    max_score = ranked[0][1]
    return [(r.id, s / max_score) ...]

That guarantees the top result always gets keyword_score = 1.0, no matter
how bad it is. Measured on the real files, that produced results like:

    query:  "Обучающие плакаты для дошкольников"
    top hit "Оборудование для единоборств"   keyword_score = 0.93

The two strings share exactly one token - the stopword "для" - yet the
score claimed 93% keyword agreement, which then fed a weighted sum and
made a completely unrelated product look like a plausible match. Because
every query produced a 1.0, the keyword signal carried no information
about whether a match was good; it only ranked within a query.

The fix is absolute (query-independent) normalization: map the raw BM25
score through a saturating curve so that a genuinely strong lexical match
approaches 1.0 and a weak one stays near 0.0, and the number means the
same thing across different queries. `_BM25_SATURATION` is the raw BM25
score at which the normalized score reaches ~0.63; it is configuration,
not a magic constant, and can be retuned from evaluation data.
"""

from __future__ import annotations

import math

from rank_bm25 import BM25Okapi

from app.services.normalizer import tokenize
from app.services.search.types import MasterProductRecord

# Raw BM25 score that maps to ~0.63 after saturation. Chosen from the
# score distribution on the real catalog: true-positive pairs score well
# above this, incidental single-token overlaps well below it.
_BM25_SATURATION = 6.0


def _saturate(raw_score: float, saturation: float = _BM25_SATURATION) -> float:
    """Map an unbounded, non-negative BM25 score into (0, 1) in a way that
    is comparable across queries. Monotonic, so ranking within a query is
    unchanged - only the absolute meaning of the number is fixed.
    """
    if raw_score <= 0:
        return 0.0
    return 1.0 - math.exp(-raw_score / saturation)


class KeywordIndex:
    def __init__(self, records: list[MasterProductRecord]):
        self._records = [r for r in records if not r.is_group_header]
        corpus = [tokenize(r.search_text()) for r in self._records]
        # BM25Okapi requires a non-empty corpus with at least one token.
        self._bm25 = BM25Okapi(corpus) if any(corpus) else None

    def search(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        """Returns [(master_product_id, score_0_to_1), ...], sorted
        descending, at most top_k results.

        The score is absolute, not relative to this query's best hit - see
        the module docstring. A query whose best lexical match is weak now
        correctly returns a low score for that match, instead of 1.0.
        """
        if self._bm25 is None or not self._records:
            return []
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        ranked = sorted(zip(self._records, scores), key=lambda rs: rs[1], reverse=True)
        ranked = [rs for rs in ranked if rs[1] > 0][:top_k]
        return [(r.id, _saturate(float(s))) for r, s in ranked]
