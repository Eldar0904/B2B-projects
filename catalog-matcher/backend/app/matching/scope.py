"""Helpers to restrict retrieval to a category / domain scope."""
from typing import Iterable, List, Optional, Set

from app.matching.base import Candidate


def allowed_product_ids(item: dict) -> Optional[Set[int]]:
    """Return allowed ids from item dict, or None if unrestricted."""
    raw = item.get("allowed_product_ids")
    if raw is None:
        return None
    return set(raw)


def blocked_product_ids(item: dict) -> Set[int]:
    raw = item.get("blocked_product_ids")
    if not raw:
        return set()
    return set(raw)


def _passes_scope(pid: int, allowed: Optional[Set[int]], blocked: Set[int]) -> bool:
    if pid in blocked:
        return False
    if allowed is not None and pid not in allowed:
        return False
    return True


def filter_ranked_pairs(
    ranked: Iterable[tuple],
    allowed: Optional[Set[int]],
    blocked: Optional[Set[int]] = None,
) -> List[tuple]:
    """Filter (product_id, score) pairs by allowed/blocked sets."""
    blocked = blocked or set()
    pairs = list(ranked)
    return [
        (pid, score) for pid, score in pairs
        if _passes_scope(pid, allowed, blocked)
    ]


def filter_candidates(
    candidates: List[Candidate],
    allowed: Optional[Set[int]],
    blocked: Optional[Set[int]] = None,
) -> List[Candidate]:
    blocked = blocked or set()
    return [
        c for c in candidates
        if _passes_scope(c.catalog_product_id, allowed, blocked)
    ]
