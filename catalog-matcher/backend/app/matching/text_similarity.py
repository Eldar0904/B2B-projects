"""Conservative fuzzy scores for product name matching."""
from rapidfuzz import fuzz


def fuzzy_name_score(query: str, choice: str) -> float:
    """
    Return 0..1 similarity that penalises subset-only matches.

    token_set_ratio alone returns 100% when the catalog name is a single
    word contained in a longer item name (e.g. «Мебель» vs «Мебель для ИЗО»).
    """
    if not query.strip() or not choice.strip():
        return 0.0

    ts = fuzz.token_set_ratio(query, choice)
    tso = fuzz.token_sort_ratio(query, choice)
    wr = fuzz.WRatio(query, choice)

    # Conservative blend — the lowest signal pulls the score down.
    score = min(ts, tso, wr) / 100.0

    q_tokens = query.lower().split()
    c_tokens = choice.lower().split()
    if len(c_tokens) <= 2 and len(q_tokens) >= 3:
        shared = len(set(q_tokens) & set(c_tokens))
        coverage = shared / max(len(set(q_tokens)), 1)
        score *= max(0.45, coverage)

    return round(score, 4)


def fuzzy_scorer_for_extract(query: str, choice: str, score_cutoff: float = 0) -> float:
    """rapidfuzz process.extract scorer (returns 0..100)."""
    return fuzzy_name_score(query, choice) * 100.0
