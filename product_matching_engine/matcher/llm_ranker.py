"""
llm_ranker.py

Re-ranks the semantic candidates for a single query item using a richer,
field-aware comparison of product name, description, technical
specifications, brand, and model — the step the spec calls "improve ranking
using an LLM".

Two rankers are provided behind the same `Ranker` interface:

- `HeuristicRanker` (default, always available): a deterministic, offline
  scorer that blends semantic similarity with weighted field-level token
  overlap (name/brand/model matches count more than generic description
  overlap) and produces a 0-100 confidence score plus a short human-readable
  explanation. No API key or network access required.

- `LLMRanker` (optional): calls a real LLM (e.g. the Anthropic API) with a
  structured prompt containing the query item and each candidate's fields,
  asking it to return a refined similarity/confidence score and a short
  explanation per candidate. This is a drop-in replacement — main.py only
  needs to swap which ranker it instantiates.

Both rankers consume/produce the same `RankedMatch` records so
`exporter.excel_exporter` never needs to know which ranker was used.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Protocol

from matcher.semantic_matcher import Candidate


@dataclass
class RankedMatch:
    catalog_code: str
    product_name: str
    brand: str
    model: str
    similarity_score: float  # 0-1 semantic similarity
    confidence: float  # 0-100
    explanation: str


class Ranker(Protocol):
    def rank(
        self,
        query: dict,
        candidates: list[dict],
    ) -> list[RankedMatch]: ...


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _overlap_ratio(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class HeuristicRanker:
    """Deterministic field-aware re-ranker. No external dependencies.

    Weights were chosen so that an exact brand+model match dominates the
    score (these are the strongest, least ambiguous signals for matching
    physical products), while name/description/spec overlap fine-tune the
    ordering among candidates that share brand/model or lack it entirely.
    """

    weight_semantic: float = 0.35
    weight_name: float = 0.25
    weight_brand: float = 0.15
    weight_model: float = 0.15
    weight_specs: float = 0.10

    def rank(self, query: dict, candidates: list[dict]) -> list[RankedMatch]:
        query_name_tokens = _tokenize(query.get("item_name", ""))
        query_desc_tokens = _tokenize(query.get("description", ""))
        query_all_tokens = query_name_tokens | query_desc_tokens

        results: list[RankedMatch] = []
        for cand in candidates:
            name_tokens = _tokenize(cand.get("name", ""))
            brand_tokens = _tokenize(cand.get("brand", ""))
            model_tokens = _tokenize(cand.get("model", ""))
            specs_tokens = _tokenize(cand.get("specs", "")) | _tokenize(cand.get("description", ""))

            name_score = _overlap_ratio(query_all_tokens, name_tokens)
            brand_score = 1.0 if brand_tokens and brand_tokens & query_all_tokens else 0.0
            model_score = 1.0 if model_tokens and model_tokens & query_all_tokens else 0.0
            specs_score = _overlap_ratio(query_desc_tokens, specs_tokens)
            semantic_score = cand.get("similarity", 0.0)

            blended = (
                self.weight_semantic * semantic_score
                + self.weight_name * name_score
                + self.weight_brand * brand_score
                + self.weight_model * model_score
                + self.weight_specs * specs_score
            )
            confidence = round(min(1.0, max(0.0, blended)) * 100, 1)

            reasons = []
            if brand_score:
                reasons.append("brand match")
            if model_score:
                reasons.append("model match")
            if name_score > 0.2:
                reasons.append(f"name overlap {name_score:.0%}")
            if specs_score > 0.2:
                reasons.append(f"spec overlap {specs_score:.0%}")
            reasons.append(f"semantic similarity {semantic_score:.0%}")
            explanation = "; ".join(reasons)

            results.append(
                RankedMatch(
                    catalog_code=cand["canonical_code"],
                    product_name=cand.get("name", ""),
                    brand=cand.get("brand", ""),
                    model=cand.get("model", ""),
                    similarity_score=round(semantic_score, 4),
                    confidence=confidence,
                    explanation=explanation,
                )
            )

        results.sort(key=lambda r: r.confidence, reverse=True)
        return results


@dataclass
class LLMRanker:
    """Optional LLM-backed re-ranker.

    Expects `client` to be an object exposing `.complete(prompt: str) -> str`
    (a thin adapter over whichever LLM SDK is in use, e.g. the Anthropic
    Python SDK) so this module has no hard dependency on any specific
    provider. The LLM is asked to return strict JSON so it can be parsed
    deterministically; if parsing fails, this ranker falls back to
    `HeuristicRanker` for that item so the pipeline never crashes on an
    LLM formatting error.
    """

    client: object
    fallback: "HeuristicRanker" = field(default_factory=HeuristicRanker)
    max_candidates: int = 20

    def _build_prompt(self, query: dict, candidates: list[dict]) -> str:
        candidate_blobs = [
            {
                "id": i,
                "product_name": c.get("name", ""),
                "brand": c.get("brand", ""),
                "model": c.get("model", ""),
                "description": c.get("description", ""),
                "specs": c.get("specs", ""),
                "semantic_similarity": round(c.get("similarity", 0.0), 4),
            }
            for i, c in enumerate(candidates[: self.max_candidates])
        ]
        return (
            "You are a procurement matching assistant. Compare the query item "
            "against each candidate product on name, description, technical "
            "specifications, brand and model. Return a JSON array, one object "
            "per candidate id, each with: id, confidence (0-100 integer), "
            "explanation (short, one sentence). Respond with ONLY the JSON array.\n\n"
            f"QUERY_ITEM: {json.dumps({'item_name': query.get('item_name', ''), 'description': query.get('description', '')})}\n\n"
            f"CANDIDATES: {json.dumps(candidate_blobs)}"
        )

    def rank(self, query: dict, candidates: list[dict]) -> list[RankedMatch]:
        prompt = self._build_prompt(query, candidates)
        try:
            raw_response = self.client.complete(prompt)
            parsed = json.loads(raw_response)
            by_id = {item["id"]: item for item in parsed}
        except Exception:
            return self.fallback.rank(query, candidates)

        results: list[RankedMatch] = []
        for i, cand in enumerate(candidates[: self.max_candidates]):
            llm_result = by_id.get(i)
            if llm_result is None:
                continue
            results.append(
                RankedMatch(
                    catalog_code=cand["canonical_code"],
                    product_name=cand.get("name", ""),
                    brand=cand.get("brand", ""),
                    model=cand.get("model", ""),
                    similarity_score=round(cand.get("similarity", 0.0), 4),
                    confidence=float(llm_result.get("confidence", 0.0)),
                    explanation=str(llm_result.get("explanation", "")),
                )
            )

        if not results:
            return self.fallback.rank(query, candidates)

        results.sort(key=lambda r: r.confidence, reverse=True)
        return results
