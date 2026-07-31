from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from environment / .env file."""

    database_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/product_matching"
    max_upload_size_mb: int = 200

    # --- Phase 2: search ---
    # Embedding backend. Default is now the real multilingual model, because
    # TF-IDF cannot bridge Kazakh <-> Russian and 12.4% of the real
    # destination file is Kazakh. If the model can't be loaded (no network on
    # first run, or the optional dependency is missing) the provider falls
    # back to TF-IDF with a warning rather than failing - see
    # search/embeddings.py::build_embedding_provider.
    #
    # LaBSE over paraphrase-multilingual-mpnet: LaBSE explicitly covers
    # Kazakh and is trained for cross-lingual alignment, so a Kazakh phrase
    # lands near its Russian equivalent in the same vector space.
    embedding_provider: str = "sentence-transformers"
    embedding_model_name: str = "sentence-transformers/LaBSE"

    # Qdrant: if qdrant_url is set, connect to a real server (e.g. the
    # docker-compose service). Otherwise fall back to embedded local mode
    # backed by qdrant_local_path (no server process required).
    qdrant_url: str | None = None
    qdrant_local_path: str = "./qdrant_storage"
    qdrant_collection_name: str = "products"

    # Retrieval
    candidates_per_method: int = 20
    top_k_candidates: int = 20


    # --- Phase 5: automatic matching ---
    # Exact identifier/normalized-name matches (spec section 11) can be
    # auto-accepted independent of the hybrid score - this is the safest
    # source of automation and doesn't depend on the still-partial
    # category/attribute/identifier scoring (see ARCHITECTURE.md Phase 5).
    enable_exact_match_auto_accept: bool = True
    exact_match_confidence: float = 0.99

    # Hybrid-score confidence thresholds.
    #
    # These are now calibrated against the measured score distribution on
    # the real files, which became possible only after scoring.py's weights
    # were fixed to sum to 1.0 (previously final_score could not exceed
    # 0.75, so a 0.95 threshold was unreachable and the hybrid auto-accept
    # path never fired at all).
    #
    # Measured separation with the v2 scorer:
    #   true matches      mean 0.975, 10th percentile 0.948
    #   non-matching pool mean 0.432, 90th percentile 0.742
    #
    # 0.88 sits below the true-match 10th percentile and well above the
    # non-match 90th percentile, so auto-accept is both useful and safe.
    high_confidence_threshold: float = 0.88
    medium_confidence_threshold: float = 0.55

    # Symmetric to the above: auto-reject (mark no_match without human
    # review) when even the best candidate is clearly not a match, so a
    # human only ever sees items actually worth their judgment. Off by
    # default (opt-in) since, like auto-accept, this changes matching
    # outcomes without a human in the loop and should be enabled
    # deliberately, per spec's "only enable after evaluation" spirit.
    # Auto-reject is OFF by default, deliberately. The user's .env had this
    # enabled with low_confidence_threshold=0.40 while final_score was
    # capped at 0.75, which silently discarded 52.7% of a 300-row sample as
    # "no match" before a human ever saw them. Auto-rejecting is far more
    # destructive than leaving an item pending: a missed review costs a
    # minute, a wrongly-discarded procurement line costs a re-run.
    enable_low_confidence_auto_reject: bool = False
    low_confidence_threshold: float = 0.20


    # --- Phase 6: reranking ---
    # "rrf" (default, offline-safe Reciprocal Rank Fusion) or "cross_encoder"
    # (real multilingual cross-encoder; requires requirements-embeddings.txt
    # + network access, same story as EMBEDDING_PROVIDER in Phase 2).
    reranker_provider: str = "rrf"
    cross_encoder_model_name: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
    rerank_pool_size: int = 20

    # LLM tie-breaker for genuinely ambiguous top-2 cases only (spec section
    # 15's "hard" tier, HANDOFF.md section 7 - Task 3). Off by default;
    # requires an API key for whichever provider is selected below. Never
    # called for every product - only when the base reranker's top-2 scores
    # are within LLM_AMBIGUITY_THRESHOLD. It only ever suggests: the human
    # reviewer still decides (see reranking.py's LLMReranker docstring) -
    # this must never become an auto-accept path.
    #
    # NOTE: this default is scaled for RERANKER_PROVIDER="rrf" (the
    # default), whose score gaps are typically well under 0.001 - see
    # ARCHITECTURE.md Phase 6 caveat. If you switch to
    # RERANKER_PROVIDER="cross_encoder" (scores roughly 0-1), raise this
    # back up to something like 0.05, or the RRF-scaled default will call
    # the LLM on almost nothing.
    enable_llm_reranker_for_hard_cases: bool = False
    llm_ambiguity_threshold: float = 0.0005

    # Which LLM provider backs the tie-breaker above. "anthropic" (uses
    # anthropic_api_key/llm_reranker_model, both below) or "gemini" (uses
    # gemini_api_key/gemini_reranker_model). Exactly one is used per
    # deployment - this is not a fallback chain.
    llm_reranker_provider: str = "anthropic"
    anthropic_api_key: str | None = None
    llm_reranker_model: str = "claude-haiku-4-5-20251001"

    # Gemini Flash-Lite: at 1,214 destination rows, calling any model per
    # item would exhaust a free-tier daily quota and take over an hour -
    # this is only ever invoked for the ambiguous top-2 cases above, never
    # per-row. Verified July 2026: the free tier covers Flash and
    # Flash-Lite only (Pro was removed from it). NOTE (HANDOFF.md section
    # 10.2, re-verified 29 July 2026): the ~1,500 requests/day figure below
    # was never actually confirmed against this project - the real
    # measured quota for gemini-2.5-flash-lite on this Google Cloud project
    # is 20 requests/day. 142 real log lines showed every call past the
    # first ~20 hitting 429 RESOURCE_EXHAUSTED and falling back to manual
    # review (fail-safe, but easy to mistake for "the LLM declined
    # everything" without reading the logs). `llm_batch_size` below exists
    # because of this. "gemini-3.5-flash-lite" does not exist as of this
    # writing - the Flash-Lite line is 2.5 and 3.1; 2.5 is the cheaper,
    # more established default here, kept configurable since Google's
    # lineup moves quickly.
    #
    # DATA PRIVACY - read before pointing this at real tenders: on the
    # FREE Gemini API tier, Google may use submitted prompts/content to
    # improve its products and human reviewers may see them (this is
    # government procurement data). The PAID tier's terms exclude prompts/
    # responses from training. If you are in the EEA, Switzerland, or the
    # UK, the paid-tier data terms apply even on the free tier. Confirm
    # this is acceptable - or move to the paid tier - before this touches
    # a real tender, not after.
    gemini_api_key: str | None = None
    gemini_reranker_model: str = "gemini-2.5-flash-lite"

    # --- LLM auto-match: a deliberate, narrow exception to "never let the
    # LLM auto-confirm" (this file's own comment above, and reranking.py's
    # LLMReranker docstring) - see reranking.py's LLMAutoMatchConfirmer for
    # the full reasoning. Off by default; a SEPARATE flag from
    # enable_llm_reranker_for_hard_cases on purpose, so turning on the safe
    # tie-breaker never silently turns on auto-accept too.
    #
    # This exists for a real, observed failure mode: a single, obviously-
    # correct candidate whose score is depressed by a literal-token
    # mismatch (a typo, e.g. "житкого"/"жидкого") that keyword_score and
    # lexical_overlap_score cannot see past, and that LLMReranker cannot
    # fix either (it only reorders among 2+ close candidates, never
    # touches final_score). This is narrower: it asks the LLM to confirm
    # ONE already-plausible candidate is the same product, nothing more.
    #
    # Reuses medium_confidence_threshold (above) as the floor below which
    # this is never even attempted - it resolves brittleness on a
    # candidate the hybrid score already finds plausible, it does not
    # invent matches from a weak signal. Every match made this way is
    # recorded with method="llm_auto_matched" (see
    # standalone_matching.save_results), distinct from an exact-string or
    # hybrid-threshold auto-match, so it stays traceable and auditable -
    # this is what keeps the exception narrow rather than a loophole.
    enable_llm_auto_match: bool = False

    # HANDOFF.md section 10.2/10.4: the confirmer used to make one API call
    # per eligible destination row. Against a real free-tier Gemini quota
    # (observed: 20 requests/day for gemini-2.5-flash-lite on this project,
    # not the ~1,500/day figure this file assumed above before that was
    # actually measured against a real project quota) that exhausted itself
    # in under a minute and every remaining row silently fell back to manual
    # review via a 429 - fail-safe, not a crash, but invisible unless you
    # went looking at the logs. `standalone_matching.build_job_result` now
    # collects every eligible row first and flushes them through
    # `LLMAutoMatchConfirmer.confirm_batch` in groups of this size, so one
    # API call covers this many rows instead of one. Lower this if a
    # provider/model's max output tokens can't fit this many YES/NO lines
    # back (unlikely at 10 - see reranking.py's `_build_batch_confirm_prompt`).
    llm_batch_size: int = 10


    # --- Phase 7: supervised learning ---
    # "sklearn_gbm" (default, always installable) or "xgboost"/"lightgbm"
    # (optional, spec's named choices; lazy-imported, see ARCHITECTURE.md
    # Phase 7 for why sklearn is the default in this environment).
    ml_model_backend: str = "sklearn_gbm"
    ml_model_artifact_path: str = "./ml_models/match_classifier.joblib"

    # Spec section 21: "500+ verified matches" before training/deploying.
    # Configurable, not hardcoded, per spec's usual "make thresholds
    # configurable" instruction.
    ml_training_min_examples: int = 500

    # Spec section 21/23: "deploy only if performance improves." The
    # trained model's test-set AUC must beat the baseline's by at least
    # this margin for should_deploy to be True.
    ml_min_improvement_margin: float = 0.02
    ml_test_split_fraction: float = 0.2

    # env_file_encoding is pinned explicitly. Without it, pydantic-settings
    # falls back to the platform default encoding, which on a Russian or
    # Kazakh Windows install is cp1251, not UTF-8. A single non-ASCII
    # character in .env then aborts startup with a bare
    # "'utf-8' codec can't decode byte 0xc2 in position 61" and no
    # indication of which file is at fault.
    #
    # Keep .env itself pure ASCII as well - see the note at the top of it.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


def _load_settings() -> "Settings":
    """Load settings, turning an unreadable .env into an actionable error.

    The raw UnicodeDecodeError names neither the file nor the fix, which
    makes it look like a bug in the migration script or the app rather than
    an encoding problem in a config file.
    """
    try:
        return Settings()
    except UnicodeDecodeError as exc:
        env_path = Path(__file__).resolve().parent.parent / ".env"
        raise RuntimeError(
            f"Could not read {env_path} as UTF-8 ({exc}).\n"
            "The file was most likely saved in a Windows codepage (cp1251) by an "
            "editor. Fix it by re-saving as UTF-8:\n"
            "  - VS Code: click the encoding in the status bar -> "
            "'Save with Encoding' -> 'UTF-8'\n"
            "  - Notepad: File -> Save As -> Encoding: UTF-8\n"
            "Or remove any non-ASCII characters (em dashes, Cyrillic text) "
            "from the comments in .env."
        ) from exc


settings = _load_settings()
