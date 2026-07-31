"""Phase 6: reranking the top-20 retrieval pool before truncating to the
top-3 shown to a human (spec section 15).

Three implementations behind one `Reranker` interface - see
ARCHITECTURE.md "Phase 6" for the full reasoning behind each:

- `RRFReranker` (default, offline-safe): Reciprocal Rank Fusion over the
  three retrieval methods' individual sub-scores.
- `CrossEncoderReranker` (optional): a real multilingual cross-encoder,
  lazily imported like Phase 2's sentence-transformers embedding provider.
- `LLMReranker` (optional): an LLM tie-breaker for genuinely ambiguous
  cases only, lazily imported, never called unless configured.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.config import settings
from app.services.search.types import ScoredCandidate


class Reranker(ABC):
    @abstractmethod
    def rerank(
        self,
        query_text: str,
        candidates: list[ScoredCandidate],
        candidate_texts: dict[str, str],
    ) -> list[ScoredCandidate]:
        """Return `candidates` re-sorted by `reranker_score` (descending).
        `candidate_texts` maps master_product_id -> the same searchable
        text used for retrieval (spec section 12's structured
        representation), for rerankers that need the actual text rather
        than just the id. Must not add or remove candidates - only
        reorder and annotate.
        """


class RRFReranker(Reranker):
    """Reciprocal Rank Fusion: combine each candidate's rank (not raw
    score) across the three retrieval methods. `k=60` is the standard RRF
    constant from the original paper (Cormack et al., 2009) and is what
    Elasticsearch/Qdrant use by default for their own RRF fusion.
    """

    def __init__(self, k: int = 60):
        self.k = k

    def rerank(
        self,
        query_text: str,
        candidates: list[ScoredCandidate],
        candidate_texts: dict[str, str],
    ) -> list[ScoredCandidate]:
        if not candidates:
            return candidates

        def ranks_for(attr: str) -> dict[str, int]:
            ordered = sorted(candidates, key=lambda c: getattr(c, attr), reverse=True)
            return {c.master_product_id: rank for rank, c in enumerate(ordered, start=1)}

        keyword_ranks = ranks_for("keyword_score")
        fuzzy_ranks = ranks_for("fuzzy_name_score")
        vector_ranks = ranks_for("embedding_score")

        for c in candidates:
            c.reranker_score = (
                1.0 / (self.k + keyword_ranks[c.master_product_id])
                + 1.0 / (self.k + fuzzy_ranks[c.master_product_id])
                + 1.0 / (self.k + vector_ranks[c.master_product_id])
            )

        return sorted(candidates, key=lambda c: c.reranker_score, reverse=True)


class CrossEncoderReranker(Reranker):
    """Real cross-encoder reranking (spec section 15, Option A). Scores
    each (query, candidate) pair jointly rather than comparing
    independently-computed embeddings, which is strictly more accurate but
    requires downloading a model from huggingface.co on first use.
    """

    def __init__(self, model_name: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"):
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:  # pragma: no cover - exercised only when misconfigured
            raise ImportError(
                "sentence-transformers is not installed. Run: "
                "pip install -r requirements-embeddings.txt, then set "
                "RERANKER_PROVIDER=cross_encoder."
            ) from exc
        self._model = CrossEncoder(model_name)

    def rerank(
        self,
        query_text: str,
        candidates: list[ScoredCandidate],
        candidate_texts: dict[str, str],
    ) -> list[ScoredCandidate]:
        if not candidates:
            return candidates
        pairs = [(query_text, candidate_texts.get(c.master_product_id, "")) for c in candidates]
        scores = self._model.predict(pairs)
        for c, score in zip(candidates, scores):
            c.reranker_score = float(score)
        return sorted(candidates, key=lambda c: c.reranker_score, reverse=True)


class LLMClient(ABC):
    """Minimal interface an LLM reranker needs - lets tests inject a fake
    client instead of calling a real API."""

    @abstractmethod
    def pick_best_candidate(self, query_text: str, candidate_texts: list[str]) -> int | None:
        """Return the 0-based index of the best candidate, or None if the
        model judges that none of them are a good match.
        """

    def confirm_batch(self, pairs: list[tuple[str, str]]) -> list[bool]:
        """Given a batch of (query_text, candidate_text) pairs, return one
        confirmed/not-confirmed decision per pair, in the same order.
        Ideally from a SINGLE underlying API call (HANDOFF.md section
        10.2/10.4: this is the fix for one-call-per-row exhausting a real
        20-request/day free quota in under a minute) - see
        AnthropicLLMClient/GeminiLLMClient for that real override.

        Concrete (not abstract) so a minimal LLMClient only has to
        implement `pick_best_candidate` to be usable at all - this default
        just calls it once per pair, which is correct but NOT
        quota-efficient, so any implementation this matters for should
        override it. (This also keeps existing test doubles that only
        implement `pick_best_candidate` working unchanged.)

        `pairs` should be `config.py`'s `llm_batch_size` or fewer when an
        override sends them in one request - callers are responsible for
        chunking, this method does not chunk internally, so it can't
        silently absorb an arbitrarily large list into a prompt too big for
        the model's output token cap.
        """
        return [self.pick_best_candidate(query_text, [candidate_text]) == 0 for query_text, candidate_text in pairs]


def _build_prompt(query_text: str, candidate_texts: list[str]) -> str:
    """Shared prompt for every LLMClient implementation - only the API call
    and response extraction differ between providers, and having them agree
    on the exact same question means switching LLM_RERANKER_PROVIDER can't
    accidentally change what's being asked, only who answers it.
    """
    options = "\n".join(f"{i}. {text}" for i, text in enumerate(candidate_texts))
    return (
        "You are matching a requested product against a small set of candidate "
        "products from a catalog. Requested product:\n"
        f"{query_text}\n\nCandidates:\n{options}\n\n"
        "Reply with ONLY the number of the best matching candidate, or the word "
        "NONE if none of them are actually the same product."
    )


def _build_batch_confirm_prompt(pairs: list[tuple[str, str]]) -> str:
    """Shared prompt for `confirm_batch` across every `LLMClient` - one
    request, N yes/no decisions, instead of N requests (HANDOFF.md section
    10.2/10.4). Numbered so `_parse_batch_confirm` can match each answer
    back to its pair even if the model reorders or skips a line.
    """
    items = "\n".join(
        f"{i}. Requested: {query_text}\n   Candidate: {candidate_text}"
        for i, (query_text, candidate_text) in enumerate(pairs, start=1)
    )
    return (
        "You are confirming whether pairs of products are the same product "
        "in a catalog matching system. For each numbered pair below, decide "
        "whether the Requested and Candidate items refer to the same "
        "product - ignore minor typos, spelling variants, and formatting "
        "differences, and focus only on whether it is actually the same "
        "product.\n\n"
        f"{items}\n\n"
        "Reply with exactly one line per pair, in order, each line "
        'containing only the pair number followed by "YES" or "NO" '
        '(for example: "1. YES"). Do not include any other text.'
    )


def _parse_batch_confirm(raw_text: str, num_items: int) -> list[bool]:
    """Extracts one confirmed/not-confirmed decision per item out of a
    model's raw batch reply. Matches lines by leading number rather than by
    position, so a model that skips, reorders, or adds a stray line doesn't
    misalign every answer after it. Any item whose line is missing or
    ambiguous defaults to `False` (not confirmed) - the same fail-safe rule
    as everywhere else in this file: an LLM that can't be understood must
    never be read as "yes".
    """
    decisions: dict[int, bool] = {}
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        digits = ""
        for ch in stripped:
            if ch.isdigit():
                digits += ch
            elif digits:
                break
        if not digits:
            continue
        try:
            index = int(digits)
        except ValueError:
            continue
        if not (1 <= index <= num_items):
            continue
        upper = stripped.upper()
        if "YES" in upper and "NO" not in upper:
            decisions[index] = True
        elif "NO" in upper:
            decisions[index] = False
    return [decisions.get(i, False) for i in range(1, num_items + 1)]


def _parse_choice(raw_text: str, num_candidates: int) -> int | None:
    """Extracts a validated candidate index out of a model's raw text
    reply, or None on anything that isn't a clean, in-range answer.

    Factored out (rather than duplicated per provider) so this - the part
    that turns a structured-output rule into an actual guarantee ("validate
    the returned index is in range", HANDOFF.md section 7) - is one
    function both clients share and one place to unit test without a
    network call, instead of two copies that could quietly drift apart.
    """
    text = raw_text.strip().upper()
    if text == "NONE":
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    try:
        index = int(digits)
    except ValueError:
        return None
    if 0 <= index < num_candidates:
        return index
    return None


class AnthropicLLMClient(LLMClient):
    def __init__(self, api_key: str, model: str = "claude-haiku-4-5-20251001"):
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - exercised only when misconfigured
            raise ImportError(
                "The anthropic package is not installed. Run: pip install anthropic, "
                "then set ANTHROPIC_API_KEY and ENABLE_LLM_RERANKER_FOR_HARD_CASES=true."
            ) from exc
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def pick_best_candidate(self, query_text: str, candidate_texts: list[str]) -> int | None:
        prompt = _build_prompt(query_text, candidate_texts)
        response = self._client.messages.create(
            model=self._model,
            max_tokens=8,
            messages=[{"role": "user", "content": prompt}],
        )
        return _parse_choice(response.content[0].text, len(candidate_texts))

    def confirm_batch(self, pairs: list[tuple[str, str]]) -> list[bool]:
        if not pairs:
            return []
        prompt = _build_batch_confirm_prompt(pairs)
        # ~6 tokens/line ("12. YES") rounded up generously - see
        # GeminiLLMClient.confirm_batch for why this cap matters.
        response = self._client.messages.create(
            model=self._model,
            max_tokens=8 * len(pairs),
            messages=[{"role": "user", "content": prompt}],
        )
        return _parse_batch_confirm(response.content[0].text, len(pairs))


class GeminiLLMClient(LLMClient):
    """Gemini Flash-Lite tie-breaker (HANDOFF.md section 7 - Task 3).

    Same contract as AnthropicLLMClient - this is the "one class" HANDOFF.md
    says adding Gemini support should be, not a parallel matching path.
    Never called for every product; see LLMReranker and the privacy/quota
    notes on config.py's gemini_api_key/gemini_reranker_model before
    enabling this against real tenders.
    """

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash-lite"):
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - exercised only when misconfigured
            raise ImportError(
                "The google-genai package is not installed. Run: pip install google-genai, "
                "then set GEMINI_API_KEY, LLM_RERANKER_PROVIDER=gemini, and "
                "ENABLE_LLM_RERANKER_FOR_HARD_CASES=true."
            ) from exc
        self._client = genai.Client(api_key=api_key)
        self._model = model
        # temperature=0 for a deterministic pick, not a creative one; a
        # short output cap since the only valid replies are a small integer
        # or the word NONE (HANDOFF.md section 7: "temperature 0, structured
        # output, and validate the returned index is in range").
        self._config = types.GenerateContentConfig(temperature=0.0, max_output_tokens=8)

    def pick_best_candidate(self, query_text: str, candidate_texts: list[str]) -> int | None:
        prompt = _build_prompt(query_text, candidate_texts)
        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=self._config,
        )
        return _parse_choice(response.text or "", len(candidate_texts))

    def confirm_batch(self, pairs: list[tuple[str, str]]) -> list[bool]:
        """HANDOFF.md section 10.2/10.4: this is the method that turns N
        eligible rows into ONE API call instead of N - the actual fix for a
        real, measured free-tier quota of 20 requests/day on this project
        (not the ~1,500/day this file originally assumed - see
        config.py's gemini_reranker_model comment).
        """
        if not pairs:
            return []
        prompt = _build_batch_confirm_prompt(pairs)
        # Each answer line is short ("12. YES") but the model needs room for
        # every line in the batch, not just one - reusing self._config's
        # fixed max_output_tokens=8 here would truncate anything past the
        # first item and silently fail the rest closed (via
        # _parse_batch_confirm's missing-line default of False).
        from google.genai import types

        batch_config = types.GenerateContentConfig(
            temperature=0.0, max_output_tokens=8 * len(pairs)
        )
        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=batch_config,
        )
        return _parse_batch_confirm(response.text or "", len(pairs))


class LLMReranker(Reranker):
    """Tie-breaker for genuinely ambiguous cases only (spec section 15's
    "hard" tier). Wraps a base reranker; only calls the LLM when that base
    reranker's top-2 candidates are within `ambiguity_threshold` of each
    other. Falls back to the base ranking unchanged if the LLM call fails,
    isn't configured, or declines to pick ("none apply").
    """

    def __init__(self, base_reranker: Reranker, llm_client: LLMClient, ambiguity_threshold: float = 0.05):
        self._base = base_reranker
        self._llm = llm_client
        self._ambiguity_threshold = ambiguity_threshold

    def rerank(
        self,
        query_text: str,
        candidates: list[ScoredCandidate],
        candidate_texts: dict[str, str],
    ) -> list[ScoredCandidate]:
        ranked = self._base.rerank(query_text, candidates, candidate_texts)
        if len(ranked) < 2:
            return ranked

        top_gap = ranked[0].reranker_score - ranked[1].reranker_score
        if top_gap >= self._ambiguity_threshold:
            return ranked  # confident enough already, no LLM call needed

        window = min(3, len(ranked))
        contenders = ranked[:window]
        contender_texts = [candidate_texts.get(c.master_product_id, "") for c in contenders]
        try:
            choice = self._llm.pick_best_candidate(query_text, contender_texts)
        except Exception:  # noqa: BLE001 - never let an LLM/network failure break matching
            return ranked

        if choice is None:
            return ranked

        chosen = contenders.pop(choice)
        chosen.reranker_score = ranked[0].reranker_score + 1e-6  # nudge to the front
        return [chosen, *contenders, *ranked[window:]]


class LLMAutoMatchConfirmer:
    """Opt-in LLM auto-match (config.py's `enable_llm_auto_match`) - a
    deliberately separate, later addition from `LLMReranker` above, and NOT
    a relaxation of HANDOFF.md section 7's "never let the LLM auto-confirm"
    rule so much as a narrow, explicitly-labeled exception to it, made with
    the user's informed consent (real procurement data, real free-tier
    data-privacy tradeoff already discussed).

    What `LLMReranker` cannot do, by design, is help a case like:

        destination: "Дозатор для житкого мыла"   (typo: житкого / жидкого)
        candidate:   "Дозатор жидкого мыла"

    `LLMReranker` only fires when 2+ retrieved candidates are too close to
    call, and even then it only reorders them - it never touches
    `final_score`, so a single, obviously-correct candidate whose score is
    depressed by a literal-token mismatch (a typo breaks keyword_score and
    lexical_overlap_score, which the fuzzy/embedding signals only partly
    compensate for) stays stuck in "needs review" no matter what
    `LLMReranker` does. This class asks a narrower, different question -
    not "which of these is best?" but "is this ONE candidate actually the
    same product?" - and, if confirmed, lets the caller promote it out of
    manual review.

    Guardrails, all deliberate:

    - `min_score` (config.py wires this to `medium_confidence_threshold`,
      0.55 by default): never invoked below this floor. This resolves
      brittleness on an ALREADY plausible candidate - it must not be asked
      to bless a near-zero-evidence guess.
    - The caller (see standalone_matching.py) is expected to record HOW a
      match was made (`auto_match_source`) distinctly from an exact-string
      or hybrid-threshold auto-match, so every `llm_auto_matched` row stays
      traceable and auditable after the fact - this is what makes the
      exception to "never auto-confirm" narrow rather than a loophole.
    - Same fail-safe rule as everywhere else here: any exception from the
      underlying LLM call is treated as "not confirmed", never as a crash.
    """

    def __init__(self, llm_client: LLMClient, min_score: float):
        self._llm = llm_client
        self._min_score = min_score

    def confirm(self, query_text: str, candidate_text: str, candidate_score: float) -> bool:
        """Single-item confirmation - kept for callers/tests that only have
        one candidate at a time. Implemented as a one-item `confirm_batch`
        call so there is exactly one code path that talks to the LLM and
        one code path that parses its answer (see that method's docstring
        for why per-item calls are the thing being fixed here).
        """
        return self.confirm_batch([(query_text, candidate_text, candidate_score)])[0]

    def confirm_batch(self, items: list[tuple[str, str, float]]) -> list[bool]:
        """Confirms a batch of (query_text, candidate_text, candidate_score)
        triples, chunking into groups of `config.py`'s `llm_batch_size` per
        underlying API call (HANDOFF.md section 10.2/10.4). Returns one
        bool per item, same order as `items`.

        Items below `min_score` are never sent to the LLM at all - same
        floor `confirm()` always enforced, just applied before chunking so
        a batch's API-call budget is spent only on candidates already
        plausible enough to be worth asking about.

        A failed batch call (network, auth, quota) fails every item in that
        batch closed (False -> manual review), never raises - same
        fail-safe rule as the rest of this file. It does not fall back to
        one-item-at-a-time, which would defeat the point of batching against
        a tight quota: if the quota is already exhausted, that fallback
        would just burn the rest of it on calls that will also 429.
        """
        results = [False] * len(items)
        eligible = [i for i, (_, _, score) in enumerate(items) if score >= self._min_score]
        batch_size = max(1, settings.llm_batch_size)

        for start in range(0, len(eligible), batch_size):
            chunk_indices = eligible[start : start + batch_size]
            pairs = [(items[i][0], items[i][1]) for i in chunk_indices]
            try:
                decisions = self._llm.confirm_batch(pairs)
            except Exception as exc:  # noqa: BLE001 - never let an LLM/network failure block a normal review
                # Logged, not swallowed silently - this used to be
                # indistinguishable from "the LLM looked and said no", which
                # made a real auth/quota/network problem invisible from
                # `docker compose logs backend`.
                print(
                    f"[llm_auto_match] batch LLM call failed ({exc!r}) for "
                    f"{len(pairs)} item(s) - falling back to manual review"
                )
                continue
            for i, decision in zip(chunk_indices, decisions):
                results[i] = decision
                query_text, candidate_text, candidate_score = items[i]
                print(
                    f"[llm_auto_match] {'CONFIRMED' if decision else 'declined'}: "
                    f"{query_text!r} vs {candidate_text!r} (score {candidate_score:.3f})"
                )
        return results


def _build_llm_client(
    llm_reranker_provider: str,
    anthropic_api_key: str | None,
    anthropic_model: str,
    gemini_api_key: str | None,
    gemini_model: str,
) -> LLMClient | None:
    """Returns None (never raises) when the selected provider's API key is
    missing - the caller falls back to the base reranker unchanged. A
    misconfigured LLM tie-breaker must never be the reason matching stops
    working, the same "fail open" rule main.py's own startup reindex
    already follows for Qdrant.
    """
    if llm_reranker_provider == "anthropic":
        if not anthropic_api_key:
            return None
        return AnthropicLLMClient(anthropic_api_key, anthropic_model)
    if llm_reranker_provider == "gemini":
        if not gemini_api_key:
            return None
        return GeminiLLMClient(gemini_api_key, gemini_model)
    return None


def build_reranker(
    provider: str,
    cross_encoder_model_name: str,
    *,
    enable_llm_tiebreaker: bool = False,
    llm_reranker_provider: str = "anthropic",
    anthropic_api_key: str | None = None,
    anthropic_model: str = "claude-haiku-4-5-20251001",
    gemini_api_key: str | None = None,
    gemini_model: str = "gemini-2.5-flash-lite",
    llm_ambiguity_threshold: float = 0.05,
) -> Reranker:
    """Builds the configured base reranker, then wraps it in an
    `LLMReranker` tie-breaker (HANDOFF.md section 7 - Task 3) if enabled.

    Before this change, `enable_llm_reranker_for_hard_cases` and every LLM
    setting in config.py were read but never consulted anywhere -
    `LLMReranker`/`AnthropicLLMClient` were fully implemented and unit
    tested (test_reranking.py) but never actually constructed by the real
    app, regardless of configuration. That gap is fixed here rather than
    worked around, since HANDOFF.md's "the architecture already supports
    this" is only true once this function actually wires it in.

    The LLM keyword-only arguments all default to "off"/empty so every
    existing caller (and every existing test) that only passes the first
    two positional arguments keeps working unchanged.
    """
    if provider == "rrf":
        base: Reranker = RRFReranker()
    elif provider == "cross_encoder":
        base = CrossEncoderReranker(cross_encoder_model_name)
    else:
        raise ValueError(f"Unknown reranker_provider: {provider}")

    if not enable_llm_tiebreaker:
        return base

    llm_client = _build_llm_client(
        llm_reranker_provider, anthropic_api_key, anthropic_model, gemini_api_key, gemini_model
    )
    if llm_client is None:
        return base

    return LLMReranker(base, llm_client, ambiguity_threshold=llm_ambiguity_threshold)
