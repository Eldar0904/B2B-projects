"""Combine multiple retrievers with Reciprocal Rank Fusion."""
from typing import List

from app.matching.base import BaseRetriever, Candidate
from app.matching.merge import rrf_merge


class HybridRetriever(BaseRetriever):
    def __init__(self, retrievers: List[BaseRetriever]):
        self.retrievers = retrievers

    def get_top_k(self, item: dict, source_id: int, k: int) -> List[Candidate]:
        if not self.retrievers:
            return []
        if len(self.retrievers) == 1:
            return self.retrievers[0].get_top_k(item, source_id, k)

        lists = [r.get_top_k(item, source_id, k) for r in self.retrievers]
        return rrf_merge(lists, k)
