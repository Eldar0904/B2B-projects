"""
Matching engine interface.

The rest of the app (API routes, DB access) only ever talks to this
interface, never to a specific algorithm. This is what lets us:
  - ship v1 with TF-IDF/fuzzy matching (no LLM, no external API calls)
  - later swap in / add an embeddings+pgvector retriever and an LLM
    reranker WITHOUT changing routes.py, models, or the frontend contract
  - add new supplier catalogs by just pointing the same engine at a
    different CatalogSource, no code changes required
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List


@dataclass
class Candidate:
    catalog_product_id: int
    score: float           # 0..1, higher is better
    explanation: str       # short human-readable rationale
    code_matched: bool = False


class BaseRetriever(ABC):
    """Stage 1: cheap, broad recall — get top-K candidates for one item."""

    @abstractmethod
    def get_top_k(self, item: dict, source_id: int, k: int) -> List[Candidate]:
        ...


class BaseFilter(ABC):
    """Stage 2: deterministic rules that narrow/reject candidates."""

    @abstractmethod
    def filter(self, item: dict, candidates: List[Candidate]) -> List[Candidate]:
        ...


class BaseRanker(ABC):
    """Stage 3: final ranking + explanation. LLM-based ranker plugs in here later."""

    @abstractmethod
    def rank(self, item: dict, candidates: List[Candidate], top_n: int) -> List[Candidate]:
        ...


class MatchingEngine:
    """Pipeline: retriever -> filter -> ranker. Swap any stage independently."""

    def __init__(self, retriever: BaseRetriever, filter_: BaseFilter, ranker: BaseRanker):
        self.retriever = retriever
        self.filter_ = filter_
        self.ranker = ranker

    def match_item(self, item: dict, source_id: int, top_k: int, top_n: int) -> List[Candidate]:
        candidates = self.retriever.get_top_k(item, source_id, top_k)
        candidates = self.filter_.filter(item, candidates)
        return self.ranker.rank(item, candidates, top_n)
