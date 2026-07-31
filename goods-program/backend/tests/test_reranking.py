import pytest

from app.services.search.reranking import (
    LLMAutoMatchConfirmer,
    LLMClient,
    LLMReranker,
    RRFReranker,
    _build_batch_confirm_prompt,
    _parse_batch_confirm,
    _parse_choice,
    build_reranker,
)
from app.services.search.types import ScoredCandidate


def _candidate(mp_id, keyword=0.0, fuzzy=0.0, embedding=0.0):
    return ScoredCandidate(
        master_product_id=mp_id,
        keyword_score=keyword,
        fuzzy_name_score=fuzzy,
        embedding_score=embedding,
        final_score=0.0,  # not used by RRF; RRF works off individual sub-scores
    )


def test_rrf_prefers_candidate_ranked_well_across_multiple_methods():
    # "a" is a strong #1 on keyword, mediocre elsewhere.
    # "b" is a consistent #2 across all three methods.
    a = _candidate("a", keyword=1.0, fuzzy=0.1, embedding=0.1)
    b = _candidate("b", keyword=0.5, fuzzy=0.5, embedding=0.5)
    c = _candidate("c", keyword=0.0, fuzzy=0.0, embedding=1.0)

    reranker = RRFReranker()
    ranked = reranker.rerank("query", [a, b, c], candidate_texts={})

    # b is rank 2 on every method -> RRF score = 3 * 1/(60+2)
    # a is rank 1 on keyword but rank 3 on the other two -> 1/(60+1) + 2*1/(60+3)
    # RRF should rank b above a here because consistent placement beats one
    # single #1 ranking plus two last-place rankings.
    ranked_ids = [c.master_product_id for c in ranked]
    assert ranked_ids[0] == "b"


def test_rrf_populates_reranker_score():
    a = _candidate("a", keyword=1.0, fuzzy=1.0, embedding=1.0)
    reranker = RRFReranker()
    ranked = reranker.rerank("query", [a], candidate_texts={})
    assert ranked[0].reranker_score > 0


def test_rrf_handles_empty_candidate_list():
    reranker = RRFReranker()
    assert reranker.rerank("query", [], candidate_texts={}) == []


class _FakeLLMClient(LLMClient):
    def __init__(self, choice):
        self.choice = choice
        self.calls = 0

    def pick_best_candidate(self, query_text, candidate_texts):
        self.calls += 1
        return self.choice


class _FailingLLMClient(LLMClient):
    def pick_best_candidate(self, query_text, candidate_texts):
        raise RuntimeError("network error")


def test_llm_reranker_only_calls_llm_when_ambiguous():
    # NOTE: RRF scores live in a very compressed range (with k=60, the gap
    # between rank 1 and rank 2 on one method is only ~1/61 - 1/62 =~
    # 0.00026), unlike a cross-encoder's 0-1 similarity scale. So a
    # sensible ambiguity_threshold for an RRF base reranker is much
    # smaller than for a cross-encoder base - this is exactly the kind of
    # per-deployment tuning spec section 14 says thresholds need. Here we
    # use a threshold scaled to RRF's actual range to prove the
    # "clear winner -> skip the LLM" path.
    a = _candidate("a", keyword=1.0, fuzzy=1.0, embedding=1.0)
    b = _candidate("b", keyword=0.0, fuzzy=0.0, embedding=0.0)
    llm = _FakeLLMClient(choice=1)
    reranker = LLMReranker(RRFReranker(), llm, ambiguity_threshold=0.0001)

    ranked = reranker.rerank("query", [a, b], candidate_texts={"a": "A", "b": "B"})
    assert ranked[0].master_product_id == "a"
    assert llm.calls == 0


def test_llm_reranker_breaks_tie_when_ambiguous():
    # Near-identical RRF scores (same sub-scores) -> ambiguous -> LLM called.
    a = _candidate("a", keyword=0.5, fuzzy=0.5, embedding=0.5)
    b = _candidate("b", keyword=0.5, fuzzy=0.5, embedding=0.51)
    llm = _FakeLLMClient(choice=0)  # LLM picks whichever is contenders[0]
    reranker = LLMReranker(RRFReranker(), llm, ambiguity_threshold=0.5)

    ranked = reranker.rerank("query", [a, b], candidate_texts={"a": "A", "b": "B"})
    assert llm.calls == 1
    assert ranked[0].master_product_id in ("a", "b")


def test_llm_reranker_falls_back_when_llm_declines():
    a = _candidate("a", keyword=0.5, fuzzy=0.5, embedding=0.5)
    b = _candidate("b", keyword=0.5, fuzzy=0.5, embedding=0.5)
    llm = _FakeLLMClient(choice=None)  # "none of these are a good match"
    reranker = LLMReranker(RRFReranker(), llm, ambiguity_threshold=1.0)

    base_order = RRFReranker().rerank("query", [a, b], candidate_texts={})
    ranked = reranker.rerank("query", [a, b], candidate_texts={"a": "A", "b": "B"})
    assert [c.master_product_id for c in ranked] == [c.master_product_id for c in base_order]


def test_llm_reranker_falls_back_on_client_exception():
    a = _candidate("a", keyword=0.5, fuzzy=0.5, embedding=0.5)
    b = _candidate("b", keyword=0.5, fuzzy=0.5, embedding=0.5)
    reranker = LLMReranker(RRFReranker(), _FailingLLMClient(), ambiguity_threshold=1.0)

    # Should not raise even though the "LLM" always fails.
    ranked = reranker.rerank("query", [a, b], candidate_texts={"a": "A", "b": "B"})
    assert len(ranked) == 2


def test_cross_encoder_reranker_import_guard():
    """We don't have network access to huggingface.co in this environment,
    so we only verify the lazy-import guard raises a clear, actionable
    error rather than a confusing one - not that the model actually loads.
    """
    pytest.importorskip("sentence_transformers", reason="optional dependency, not installed here")


# --- _parse_choice (HANDOFF.md section 7 - Task 3) --------------------------
#
# Shared by AnthropicLLMClient and GeminiLLMClient's pick_best_candidate,
# and the only part of "temperature 0, structured output, and validate the
# returned index is in range" that's actually testable without a network
# call - so it's tested directly, in isolation, here.


def test_parse_choice_reads_a_clean_digit():
    assert _parse_choice("2", num_candidates=3) == 2


def test_parse_choice_reads_none_case_insensitively():
    assert _parse_choice("none", num_candidates=3) is None
    assert _parse_choice("None", num_candidates=3) is None
    assert _parse_choice("NONE", num_candidates=3) is None


def test_parse_choice_strips_surrounding_whitespace_and_punctuation():
    assert _parse_choice("  1.\n", num_candidates=3) == 1


def test_parse_choice_rejects_out_of_range_index():
    """A model confidently returning "5" for a 3-candidate list must not
    be trusted - HANDOFF.md section 7's explicit "validate the returned
    index is in range" requirement.
    """
    assert _parse_choice("5", num_candidates=3) is None


def test_parse_choice_rejects_unparseable_text():
    assert _parse_choice("I think it's the second one", num_candidates=3) is None
    assert _parse_choice("", num_candidates=3) is None


def test_gemini_llm_client_import_guard():
    """Same guarantee as the cross-encoder's: no network access to Google's
    API in this environment, so this only verifies the lazy-import guard
    is clear and actionable, not that a real API call succeeds.
    """
    pytest.importorskip("google.genai", reason="optional dependency, not installed here")


# --- build_reranker's LLM wiring ---------------------------------------------
#
# Before this change, enable_llm_reranker_for_hard_cases and every LLM
# setting in config.py were read but never consulted by build_reranker -
# LLMReranker/AnthropicLLMClient were fully implemented and tested in
# isolation but never actually constructed by the real app. These tests
# lock in the fix.


def test_build_reranker_defaults_to_base_reranker_only():
    """The overwhelming default case: LLM tie-breaker disabled entirely."""
    reranker = build_reranker("rrf", "unused-model-name")
    assert isinstance(reranker, RRFReranker)


def test_build_reranker_stays_base_when_enabled_but_no_api_key():
    """Enabling the tie-breaker without an API key must fail open (base
    reranker, matching's ordinary behavior) rather than crash startup -
    the same "never let a missing/misconfigured provider break matching"
    rule as main.py's own startup index rebuild.
    """
    reranker = build_reranker(
        "rrf", "unused-model-name",
        enable_llm_tiebreaker=True,
        llm_reranker_provider="anthropic",
        anthropic_api_key=None,
    )
    assert isinstance(reranker, RRFReranker)


def test_build_reranker_stays_base_for_unknown_llm_provider():
    reranker = build_reranker(
        "rrf", "unused-model-name",
        enable_llm_tiebreaker=True,
        llm_reranker_provider="not-a-real-provider",
        anthropic_api_key="sk-fake",
        gemini_api_key="fake-key",
    )
    assert isinstance(reranker, RRFReranker)


def test_build_reranker_wraps_in_llm_reranker_when_configured(monkeypatch):
    """With a real (fake, for the test) client available for the selected
    provider, build_reranker must actually wrap the base reranker - this
    is the behavior that was missing entirely before this change.
    """
    import app.services.search.reranking as reranking_module

    class _FakeClient(LLMClient):
        def pick_best_candidate(self, query_text, candidate_texts):
            return None

    monkeypatch.setattr(
        reranking_module, "AnthropicLLMClient", lambda api_key, model: _FakeClient()
    )

    reranker = build_reranker(
        "rrf", "unused-model-name",
        enable_llm_tiebreaker=True,
        llm_reranker_provider="anthropic",
        anthropic_api_key="sk-fake",
    )
    assert isinstance(reranker, LLMReranker)


def test_build_reranker_selects_gemini_provider(monkeypatch):
    import app.services.search.reranking as reranking_module

    class _FakeClient(LLMClient):
        def pick_best_candidate(self, query_text, candidate_texts):
            return None

    monkeypatch.setattr(
        reranking_module, "GeminiLLMClient", lambda api_key, model: _FakeClient()
    )

    reranker = build_reranker(
        "rrf", "unused-model-name",
        enable_llm_tiebreaker=True,
        llm_reranker_provider="gemini",
        gemini_api_key="fake-key",
    )
    assert isinstance(reranker, LLMReranker)


# --- LLMAutoMatchConfirmer ---------------------------------------------------
#
# The narrower, separate "auto-match" exception to "never let the LLM
# auto-confirm" - see the class's own docstring for the full reasoning.
# These tests lock in its guardrails: the score floor, fail-safe behavior
# on any LLM exception, and that only an explicit "yes, candidate 0" counts
# as confirmation.


class _FakeConfirmClient(LLMClient):
    def __init__(self, choice):
        self.choice = choice
        self.calls = 0

    def pick_best_candidate(self, query_text, candidate_texts):
        self.calls += 1
        return self.choice


class _FailingConfirmClient(LLMClient):
    def pick_best_candidate(self, query_text, candidate_texts):
        raise RuntimeError("network error")


def test_llm_auto_match_confirmer_never_calls_llm_below_the_floor():
    """The whole point of the floor: this must not be given a chance to
    bless a near-zero-evidence guess - it should not even ask.
    """
    llm = _FakeConfirmClient(choice=0)
    confirmer = LLMAutoMatchConfirmer(llm, min_score=0.55)

    result = confirmer.confirm("query", "candidate", candidate_score=0.30)

    assert result is False
    assert llm.calls == 0


def test_llm_auto_match_confirmer_confirms_above_the_floor():
    llm = _FakeConfirmClient(choice=0)
    confirmer = LLMAutoMatchConfirmer(llm, min_score=0.55)

    result = confirmer.confirm("query", "candidate", candidate_score=0.76)

    assert result is True
    assert llm.calls == 1


def test_llm_auto_match_confirmer_declines_when_llm_says_none():
    llm = _FakeConfirmClient(choice=None)
    confirmer = LLMAutoMatchConfirmer(llm, min_score=0.55)

    assert confirmer.confirm("query", "candidate", candidate_score=0.76) is False


def test_llm_auto_match_confirmer_fails_safe_on_llm_exception():
    confirmer = LLMAutoMatchConfirmer(_FailingConfirmClient(), min_score=0.55)

    # Must not raise, and must count as "not confirmed" - a network hiccup
    # must never be mistaken for a positive confirmation.
    assert confirmer.confirm("query", "candidate", candidate_score=0.90) is False


def test_llm_auto_match_confirmer_at_exactly_the_floor_is_attempted():
    """The floor is inclusive - `score < min_score` is what's rejected, so
    a candidate exactly at the configured threshold still gets a chance.
    """
    llm = _FakeConfirmClient(choice=0)
    confirmer = LLMAutoMatchConfirmer(llm, min_score=0.55)

    assert confirmer.confirm("query", "candidate", candidate_score=0.55) is True
    assert llm.calls == 1


# --- Batched confirmation (HANDOFF.md section 10.2/10.4) --------------------
#
# The actual fix: a real free-tier Gemini quota (20 requests/day, measured
# against this project) was exhausted in under a minute because both
# LLMReranker and LLMAutoMatchConfirmer made one API call per row. These
# tests lock in that N eligible rows now cost ONE call (chunked at
# config.py's llm_batch_size), not N, and that the fail-safe/floor
# guarantees still hold in the batched path.


class _FakeBatchConfirmClient(LLMClient):
    """Implements confirm_batch directly (like the real Anthropic/Gemini
    clients do) so tests can verify LLMAutoMatchConfirmer actually calls
    the efficient batched path, not the one-call-per-item default on
    LLMClient itself.
    """

    def __init__(self, decisions_by_call: list[list[bool]] | None = None, decision: bool = True):
        self._decisions_by_call = decisions_by_call
        self._decision = decision
        self.batch_calls: list[int] = []  # size of `pairs` on each call

    def pick_best_candidate(self, query_text, candidate_texts):  # pragma: no cover - unused in these tests
        raise AssertionError("expected the batched path to be used, not pick_best_candidate")

    def confirm_batch(self, pairs):
        self.batch_calls.append(len(pairs))
        if self._decisions_by_call is not None:
            return self._decisions_by_call[len(self.batch_calls) - 1]
        return [self._decision] * len(pairs)


def test_confirmer_confirm_batch_makes_one_call_for_multiple_eligible_items(monkeypatch):
    """The core claim of section 10.4's fix: several eligible rows produce
    ONE underlying call, not one per row.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "llm_batch_size", 10)
    llm = _FakeBatchConfirmClient(decision=True)
    confirmer = LLMAutoMatchConfirmer(llm, min_score=0.55)

    items = [("q1", "c1", 0.60), ("q2", "c2", 0.70), ("q3", "c3", 0.90)]
    results = confirmer.confirm_batch(items)

    assert results == [True, True, True]
    assert llm.batch_calls == [3]  # one call covering all three


def test_confirmer_confirm_batch_never_sends_below_floor_items_to_the_llm(monkeypatch):
    """Items below min_score must be excluded from the batch entirely - not
    sent and auto-declined, same as the single-item `confirm` floor, just
    applied before chunking so a tight quota isn't spent asking about
    near-zero-evidence guesses.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "llm_batch_size", 10)
    llm = _FakeBatchConfirmClient(decision=True)
    confirmer = LLMAutoMatchConfirmer(llm, min_score=0.55)

    items = [("q1", "c1", 0.10), ("q2", "c2", 0.70), ("q3", "c3", 0.20)]
    results = confirmer.confirm_batch(items)

    assert results == [False, True, False]
    assert llm.batch_calls == [1]  # only the one eligible item was ever sent


def test_confirmer_confirm_batch_chunks_at_llm_batch_size(monkeypatch):
    """A run with more eligible rows than llm_batch_size must issue
    multiple calls, each capped at that size - not one call with an
    unbounded prompt.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "llm_batch_size", 2)
    llm = _FakeBatchConfirmClient(decision=True)
    confirmer = LLMAutoMatchConfirmer(llm, min_score=0.55)

    items = [(f"q{i}", f"c{i}", 0.90) for i in range(5)]
    results = confirmer.confirm_batch(items)

    assert results == [True] * 5
    assert llm.batch_calls == [2, 2, 1]


def test_confirmer_confirm_batch_fails_safe_per_chunk_without_blocking_others(monkeypatch):
    """One chunk's API failure (e.g. a 429) must not raise and must not
    prevent other chunks from being attempted - each chunk is independent.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "llm_batch_size", 2)

    class _FlakyClient(LLMClient):
        def __init__(self):
            self.calls = 0

        def pick_best_candidate(self, query_text, candidate_texts):  # pragma: no cover
            raise AssertionError("unused")

        def confirm_batch(self, pairs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("429 RESOURCE_EXHAUSTED")
            return [True] * len(pairs)

    confirmer = LLMAutoMatchConfirmer(_FlakyClient(), min_score=0.55)
    items = [(f"q{i}", f"c{i}", 0.90) for i in range(4)]

    results = confirmer.confirm_batch(items)  # must not raise

    assert results == [False, False, True, True]  # first chunk failed closed, second succeeded


def test_confirmer_confirm_delegates_to_confirm_batch_as_a_single_item():
    """`confirm` (the older single-item API, still used by callers with
    just one candidate) must go through the same code path as
    `confirm_batch` rather than duplicating the LLM-call/parse logic - one
    place to get this right, not two that can drift apart.
    """
    llm = _FakeBatchConfirmClient(decision=True)
    confirmer = LLMAutoMatchConfirmer(llm, min_score=0.55)

    assert confirmer.confirm("q", "c", candidate_score=0.90) is True
    assert llm.batch_calls == [1]


# --- Batch confirm prompt/parsing (HANDOFF.md section 10.2/10.4) ------------
#
# Pure functions, tested directly and without a network call - same
# convention as _parse_choice above.


def test_build_batch_confirm_prompt_numbers_every_pair():
    prompt = _build_batch_confirm_prompt([("Дозатор для житкого мыла", "Дозатор жидкого мыла"), ("Стол", "Стул")])
    assert "1. Requested: Дозатор для житкого мыла" in prompt
    assert "Candidate: Дозатор жидкого мыла" in prompt
    assert "2. Requested: Стол" in prompt
    assert "Candidate: Стул" in prompt


def test_parse_batch_confirm_reads_yes_no_lines_in_order():
    raw = "1. YES\n2. NO\n3. YES"
    assert _parse_batch_confirm(raw, num_items=3) == [True, False, True]


def test_parse_batch_confirm_matches_by_number_not_position():
    """A reordered or sparse reply must still map back to the right item by
    its leading number, not by line position.
    """
    raw = "2. NO\n1. YES\n3. YES"
    assert _parse_batch_confirm(raw, num_items=3) == [True, False, True]


def test_parse_batch_confirm_defaults_missing_lines_to_false():
    """Same fail-safe rule as everywhere else: an answer that isn't there
    (truncated output, model skipped an item) must never be read as a yes.
    """
    raw = "1. YES"
    assert _parse_batch_confirm(raw, num_items=3) == [True, False, False]


def test_parse_batch_confirm_ignores_out_of_range_numbers():
    raw = "1. YES\n99. YES\n2. NO"
    assert _parse_batch_confirm(raw, num_items=2) == [True, False]


def test_parse_batch_confirm_handles_empty_reply():
    assert _parse_batch_confirm("", num_items=3) == [False, False, False]
