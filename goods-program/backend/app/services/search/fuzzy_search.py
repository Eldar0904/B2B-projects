"""Fuzzy name matching.

Catches near-duplicate / typo'd product names that BM25's token-overlap
model can miss, e.g. "Столь детский" vs "Стол детский", or names that
differ only by word order.

--- Why token_sort_ratio was the wrong scorer here -----------------------

The previous implementation used `fuzz.token_sort_ratio` alone. That
scorer sorts the tokens of both strings and then compares them as whole
strings, which means it penalizes length differences heavily. In this
dataset the two sides are systematically different lengths: the
destination file's names average 32 characters while the master catalog's
average 93, because the catalog appends full specifications to the name.

The result, measured on real pairs from these files:

    "манеж детский"    vs "манеж детский размерами 830х680 мм"
        token_sort_ratio =  55      token_set_ratio = 100
    "грелка резиновая" vs "грелка резиновая объемом 2 л"
        token_sort_ratio =  73      token_set_ratio = 100

So the correct catalog entry - which literally contains the destination
name verbatim - was scored 55% similar and pushed out of the top 3.

`token_set_ratio` treats the shared token set as the basis of comparison
and does not punish the extra specification text, which is exactly the
asymmetry we have. We take the max of `token_set_ratio` and `WRatio`
(rapidfuzz's tuned general-purpose combiner) so that neither pure
containment nor pure typo-similarity is missed.

Note that `token_set_ratio` alone is too generous in the other direction
(any short query is ~100% contained in some long name), which is why the
final score in `scoring.py` no longer leans on fuzzy alone and pairs it
with the IDF-weighted overlap signal in `lexical_overlap.py`.
"""

from __future__ import annotations

from rapidfuzz import fuzz, process

from app.services.search.types import MasterProductRecord


class FuzzyIndex:
    def __init__(self, records: list[MasterProductRecord]):
        self._records = [r for r in records if not r.is_group_header]
        self._choices = {r.id: r.normalized_name for r in self._records}

    def search(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        """Returns [(master_product_id, score_0_to_1), ...].

        Runs two scorers and keeps the better score per candidate:
        `token_set_ratio` for the short-query / long-catalog-name case, and
        `WRatio` for typos and partial rewrites.
        """
        if not self._choices or not query.strip():
            return []

        best: dict[str, float] = {}
        for scorer in (fuzz.token_set_ratio, fuzz.WRatio):
            results = process.extract(
                query,
                self._choices,
                scorer=scorer,
                limit=top_k,
            )
            # rapidfuzz's process.extract on a dict returns (choice, score, key)
            for _choice, score, key in results:
                if score <= 0:
                    continue
                normalized = score / 100.0
                if normalized > best.get(key, 0.0):
                    best[key] = normalized

        ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
        return ranked[:top_k]
