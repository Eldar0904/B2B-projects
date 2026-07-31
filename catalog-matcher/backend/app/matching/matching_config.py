"""
Runtime configuration for a single matching run.

Presets (matching_mode):
  fast     — code + TF-IDF
  balanced — code + TF-IDF + fuzzy text + embeddings (if available)
  semantic — same as balanced for now (LLM rerank is a future stage)
"""
from dataclasses import dataclass, field
from typing import Optional


_MODE_DEFAULTS = {
    "fast": {
        "use_code_matching": True,
        "use_tfidf": True,
        "use_fuzzy_text": False,
        "use_embeddings": False,
        "top_k_candidates": 20,
    },
    "balanced": {
        "use_code_matching": True,
        "use_tfidf": True,
        "use_fuzzy_text": True,
        "use_embeddings": True,
        "top_k_candidates": 50,
    },
    "semantic": {
        "use_code_matching": True,
        "use_tfidf": True,
        "use_fuzzy_text": True,
        "use_embeddings": True,
        "top_k_candidates": 50,
    },
}


@dataclass
class MatchingConfig:
    matching_mode: str = "balanced"
    top_k_candidates: int = 50
    top_n_results: int = 3
    min_similarity_score: float = 0.35

    use_code_matching: bool = True
    use_tfidf: bool = True
    use_fuzzy_text: bool = True
    use_embeddings: bool = True

    embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"
    code_fuzzy_threshold: float = 0.80
    fuzzy_text_threshold: float = 0.55

    use_category_filter: bool = True
    infer_category_if_missing: bool = True

    @classmethod
    def from_request(
        cls,
        matching_mode: str = "balanced",
        top_k_candidates: Optional[int] = None,
        top_n_results: Optional[int] = None,
        min_similarity_score: Optional[float] = None,
        use_code_matching: Optional[bool] = None,
        use_tfidf: Optional[bool] = None,
        use_fuzzy_text: Optional[bool] = None,
        use_embeddings: Optional[bool] = None,
        embedding_model: Optional[str] = None,
        use_category_filter: Optional[bool] = None,
        infer_category_if_missing: Optional[bool] = None,
        **_,
    ) -> "MatchingConfig":
        mode = matching_mode if matching_mode in _MODE_DEFAULTS else "balanced"
        defaults = _MODE_DEFAULTS[mode]

        cfg = cls(
            matching_mode=mode,
            top_k_candidates=top_k_candidates if top_k_candidates is not None else defaults["top_k_candidates"],
            top_n_results=top_n_results if top_n_results is not None else 3,
            min_similarity_score=min_similarity_score if min_similarity_score is not None else 0.35,
            use_code_matching=use_code_matching if use_code_matching is not None else defaults["use_code_matching"],
            use_tfidf=use_tfidf if use_tfidf is not None else defaults["use_tfidf"],
            use_fuzzy_text=use_fuzzy_text if use_fuzzy_text is not None else defaults["use_fuzzy_text"],
            use_embeddings=use_embeddings if use_embeddings is not None else defaults["use_embeddings"],
            embedding_model=embedding_model or "paraphrase-multilingual-MiniLM-L12-v2",
            use_category_filter=use_category_filter if use_category_filter is not None else True,
            infer_category_if_missing=infer_category_if_missing if infer_category_if_missing is not None else True,
        )
        return cfg

    def engine_name(self) -> str:
        parts = [self.matching_mode]
        if self.use_code_matching:
            parts.append("code")
        if self.use_tfidf:
            parts.append("tfidf")
        if self.use_fuzzy_text:
            parts.append("fuzzy")
        if self.use_embeddings:
            parts.append("embed")
        return "_".join(parts)

    def to_dict(self) -> dict:
        return {
            "matching_mode": self.matching_mode,
            "top_k_candidates": self.top_k_candidates,
            "top_n_results": self.top_n_results,
            "min_similarity_score": self.min_similarity_score,
            "use_code_matching": self.use_code_matching,
            "use_tfidf": self.use_tfidf,
            "use_fuzzy_text": self.use_fuzzy_text,
            "use_embeddings": self.use_embeddings,
            "embedding_model": self.embedding_model,
            "use_category_filter": self.use_category_filter,
            "infer_category_if_missing": self.infer_category_if_missing,
        }
