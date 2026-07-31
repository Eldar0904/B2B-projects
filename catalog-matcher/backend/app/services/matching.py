"""Phase 3: matching workflow (destination product -> top 3 candidates ->
user selection) and Phase 4: feedback storage (save every decision).

Keyed by a destination upload's own id (see ARCHITECTURE.md "Phase 3" for
why this doesn't introduce a separate matching-job table).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import DestinationProduct, Feedback, MasterProduct, Match
from app.services.search.index_manager import CatalogSearchIndex
from app.services.search.types import ScoredCandidate


@dataclass
class MatchingProgress:
    total: int
    pending: int
    matched: int
    no_match: int


def get_progress(db: Session, upload_id: str) -> MatchingProgress:
    rows = (
        db.query(DestinationProduct.status, func.count(DestinationProduct.id))
        .filter(DestinationProduct.upload_id == upload_id)
        .group_by(DestinationProduct.status)
        .all()
    )
    counts = {status: count for status, count in rows}
    total = sum(counts.values())
    return MatchingProgress(
        total=total,
        pending=counts.get("pending", 0),
        matched=counts.get("matched", 0),
        no_match=counts.get("no_match", 0),
    )


def get_next_pending(
    db: Session, upload_id: str, strategy: str = "sequential"
) -> DestinationProduct | None:
    """Phase 8: `strategy="uncertainty"` prioritizes the product with the
    smallest `uncertainty_margin` (most ambiguous top-2 candidates) first,
    per spec section 24. Products that haven't been prioritized yet
    (`uncertainty_margin IS NULL`) sort after prioritized ones but still
    follow `source_row` order among themselves, so calling this with
    `strategy="uncertainty"` before ever running
    `POST /api/matching/{upload_id}/prioritize` degrades gracefully to
    plain sequential order instead of erroring.
    """
    query = db.query(DestinationProduct).filter(
        DestinationProduct.upload_id == upload_id, DestinationProduct.status == "pending"
    )
    if strategy == "uncertainty":
        query = query.order_by(
            DestinationProduct.uncertainty_margin.is_(None),  # False (0) sorts before True (1)
            DestinationProduct.uncertainty_margin,
            DestinationProduct.source_row,
        )
    else:
        query = query.order_by(DestinationProduct.source_row)
    return query.first()


def build_explanation(candidate: ScoredCandidate) -> list[str]:
    """Turn a candidate's real scores into short factual statements (spec
    section 18) - never a fake/hardcoded explanation.
    """
    reasons: list[str] = []

    # Coverage first: it is the most decisive signal and the most legible
    # to a human reviewer ("does this candidate account for what I asked
    # for?"). See search/lexical_overlap.py.
    #
    # Only stated when there IS coverage. A bare "shares no significant
    # terms" would otherwise suppress the genuinely-no-signal fallback at
    # the bottom of this function, which is the more useful message when
    # every score is zero.
    if candidate.lexical_overlap_score >= 0.99:
        reasons.append("Covers every significant word in the request")
    elif candidate.lexical_overlap_score > 0:
        reasons.append(
            f"Covers {round(candidate.lexical_overlap_score * 100)}% of the request's key terms"
        )

    if candidate.keyword_score >= 0.95:
        reasons.append("Exact keyword overlap")
    elif candidate.keyword_score > 0:
        reasons.append(f"{round(candidate.keyword_score * 100)}% keyword overlap")

    if candidate.fuzzy_name_score >= 0.95:
        reasons.append("Near-identical product name")
    elif candidate.fuzzy_name_score > 0:
        reasons.append(f"{round(candidate.fuzzy_name_score * 100)}% fuzzy name similarity")

    if candidate.embedding_score > 0:
        reasons.append(f"{round(candidate.embedding_score * 100)}% semantic similarity")

    if candidate.matched_by:
        reasons.append(f"Found by {len(candidate.matched_by)} of 3 retrieval methods ({', '.join(sorted(candidate.matched_by))})")

    if not reasons:
        reasons.append("Low-confidence candidate; no strong signal from any retrieval method")

    return reasons


@dataclass
class CandidateWithProduct:
    candidate: ScoredCandidate
    master_product: MasterProduct
    explanation: list[str]


def get_top_candidates(
    db: Session, index: CatalogSearchIndex, destination_product: DestinationProduct, top_k: int = 3
) -> list[CandidateWithProduct]:
    """Phase 6: uses the reranked pool (spec section 15), not raw
    Phase 2 retrieval order - see `CatalogSearchIndex.search_reranked`.
    """
    query_text = destination_product.normalized_name or destination_product.product_name or ""
    scored = index.search_reranked(query_text, top_k=top_k)
    results = []
    for c in scored:
        mp = db.get(MasterProduct, c.master_product_id)
        if mp is None:
            continue
        results.append(CandidateWithProduct(candidate=c, master_product=mp, explanation=build_explanation(c)))
    return results


def _candidate_data_json(
    destination_product: DestinationProduct,
    candidates: list[ScoredCandidate],
    db: Session,
    selected_master_product_id: str | None,
    decision: str,
) -> dict:
    """Build the feedback JSON shape from spec section 20."""
    selected_name = None
    if selected_master_product_id:
        mp = db.get(MasterProduct, selected_master_product_id)
        selected_name = mp.product_name if mp else None

    return {
        "destination_product": destination_product.product_name,
        "selected_master_product": selected_name,
        "candidates": [
            {"id": c.master_product_id, "rank": rank, "score": round(c.final_score, 4)}
            for rank, c in enumerate(candidates, start=1)
        ],
        "decision": decision,
    }


def _decision_type_for_confirm(rank: int) -> str:
    """rank 1-3 -> came from the top-3 hybrid search shown to the user.
    rank 0 (sentinel used by the frontend) -> came from manual search
    instead (spec section 20's `manual_search_selected`).
    """
    if rank and 1 <= rank <= 3:
        return "user_selected"
    return "manual_search_selected"


def confirm_match(
    db: Session,
    destination_product: DestinationProduct,
    master_product_id: str,
    rank: int,
    candidates: list[ScoredCandidate],
) -> Match:
    decision_type = _decision_type_for_confirm(rank)

    match = Match(
        destination_product_id=destination_product.id,
        master_product_id=master_product_id,
        confidence=next((c.final_score for c in candidates if c.master_product_id == master_product_id), None),
        match_type="user_selected",
        method="hybrid_top3" if decision_type == "user_selected" else "manual_search",
        is_confirmed=True,
    )
    db.add(match)

    feedback = Feedback(
        destination_product_id=destination_product.id,
        selected_master_product_id=master_product_id,
        decision_type=decision_type,
        candidate_data=_candidate_data_json(destination_product, candidates, db, master_product_id, decision_type),
    )
    db.add(feedback)

    destination_product.status = "matched"
    db.flush()
    return match


def reject_match(
    db: Session,
    destination_product: DestinationProduct,
    candidates: list[ScoredCandidate] | None = None,
) -> Feedback:
    candidates = candidates or []
    feedback = Feedback(
        destination_product_id=destination_product.id,
        selected_master_product_id=None,
        decision_type="no_match",
        candidate_data=_candidate_data_json(destination_product, candidates, db, None, "no_match"),
    )
    db.add(feedback)
    destination_product.status = "no_match"
    db.flush()
    return feedback


@dataclass
class FeedbackStats:
    total: int
    user_selected: int
    manual_search_selected: int
    no_match: int
    auto_accepted: int
    user_rejected: int
    auto_rejected: int


def get_feedback_stats(db: Session, upload_id: str) -> FeedbackStats:
    rows = (
        db.query(Feedback.decision_type, func.count(Feedback.id))
        .join(DestinationProduct, Feedback.destination_product_id == DestinationProduct.id)
        .filter(DestinationProduct.upload_id == upload_id)
        .group_by(Feedback.decision_type)
        .all()
    )
    counts = {decision_type: count for decision_type, count in rows}
    return FeedbackStats(
        total=sum(counts.values()),
        user_selected=counts.get("user_selected", 0),
        manual_search_selected=counts.get("manual_search_selected", 0),
        no_match=counts.get("no_match", 0),
        auto_accepted=counts.get("auto_accepted", 0),
        user_rejected=counts.get("user_rejected", 0),
        auto_rejected=counts.get("auto_rejected", 0),
    )


# --- Phase 5: automatic matching ---


def find_exact_match(
    db: Session, destination_product: DestinationProduct, master_upload_id: str | None = None
) -> MasterProduct | None:
    """Spec section 11: exact identifier or exact normalized-name match,
    checked before any hybrid scoring. Independent of Phase 2's partial
    scoring - see ARCHITECTURE.md "Phase 5" for why this is the trustworthy
    source of automation today.

    `master_upload_id` defaults to None (search every MasterProduct row
    ever ingested) - the right behavior for the main review tab, where the
    master catalog is intentionally one continuously-updated global catalog
    across however many master uploads have happened over time.

    Pass a specific `master_upload_id` to scope the check to just that
    upload instead. The standalone matching wizard (see
    `standalone_matching._classify_destination_product`) needs this: it
    already scopes its hybrid-search index to just the freshly-ingested
    master upload, but this function queries MasterProduct directly and
    bypasses the index entirely - so without this scope, an old, unrelated
    master upload sitting in the table (e.g. from an earlier wizard run, or
    a mistaken catalog file dropped in during testing) could still produce
    a bogus "exact match" here even after the index-side fix.
    """
    query = db.query(MasterProduct).filter(
        MasterProduct.is_group_header == False,  # noqa: E712
        MasterProduct.is_active.is_(True),  # HANDOFF.md section 13 - soft-deleted rows never match
    )
    if master_upload_id is not None:
        query = query.filter(MasterProduct.upload_id == master_upload_id)

    if destination_product.external_id:
        by_code = query.filter(MasterProduct.external_id == destination_product.external_id).first()
        if by_code is not None:
            return by_code

    if destination_product.normalized_name:
        by_name = query.filter(MasterProduct.normalized_name == destination_product.normalized_name).first()
        if by_name is not None:
            return by_name

    return None


def classify_confidence(final_score: float, high: float, medium: float) -> str:
    """Spec section 16: HIGH (auto-accept-eligible) / MEDIUM (show top-3) /
    LOW (still show top-3, but flag "no reliable match found")."""
    if final_score >= high:
        return "high"
    if final_score >= medium:
        return "medium"
    return "low"


def auto_accept_match(
    db: Session,
    destination_product: DestinationProduct,
    master_product: MasterProduct,
    confidence: float,
    method: str,
) -> Match:
    """Records an automatic match exactly like a human confirmation, except
    `decision_type = "auto_accepted"` and no candidate list was ever shown
    to a human (spec section 20's `auto_accepted` decision type).
    """
    match = Match(
        destination_product_id=destination_product.id,
        master_product_id=master_product.id,
        confidence=confidence,
        match_type="auto_accepted",
        method=method,
        is_confirmed=True,
    )
    db.add(match)

    feedback = Feedback(
        destination_product_id=destination_product.id,
        selected_master_product_id=master_product.id,
        decision_type="auto_accepted",
        candidate_data={
            "destination_product": destination_product.product_name,
            "selected_master_product": master_product.product_name,
            "candidates": [{"id": master_product.id, "rank": 1, "score": round(confidence, 4)}],
            "decision": "auto_accepted",
        },
    )
    db.add(feedback)

    destination_product.status = "matched"
    db.flush()
    return match


def auto_reject_match(db: Session, destination_product: DestinationProduct, top_score: float | None) -> Feedback:
    """Symmetric to `auto_accept_match`: marks a destination product
    `no_match` without ever showing it to a human, because even the best
    candidate found was clearly not a match (or nothing was found at
    all). Uses a distinct `decision_type` ("auto_rejected") rather than
    plain "no_match" so feedback stats can tell automatic rejections
    apart from a human explicitly clicking "None of these" - this is an
    extension beyond spec section 20's literal decision-type list, added
    because the spec's automation examples (section 24) are symmetric in
    spirit: confident enough either way should not need a human.
    """
    feedback = Feedback(
        destination_product_id=destination_product.id,
        selected_master_product_id=None,
        decision_type="auto_rejected",
        candidate_data={
            "destination_product": destination_product.product_name,
            "selected_master_product": None,
            "candidates": [] if top_score is None else [{"id": None, "rank": 1, "score": round(top_score, 4)}],
            "decision": "auto_rejected",
        },
    )
    db.add(feedback)
    destination_product.status = "no_match"
    db.flush()
    return feedback


def try_auto_match(
    db: Session,
    index: CatalogSearchIndex,
    destination_product: DestinationProduct,
    high_threshold: float,
    low_threshold: float | None = None,
) -> Match | None:
    """Attempt automatic matching for one destination product. Returns the
    created Match if it auto-accepted, None otherwise - which includes
    both "needs human review" and "auto-rejected" cases (check
    `destination_product.status` to tell those apart: "pending" means
    still needs review, "no_match" means auto-rejected).

    `low_threshold`: if given, and no exact/threshold match qualifies,
    auto-reject when the top candidate's score is below it (or there are
    no candidates at all) instead of leaving the item pending.
    """
    exact = find_exact_match(db, destination_product)
    if exact is not None:
        return auto_accept_match(db, destination_product, exact, confidence=0.99, method="exact_match")

    query_text = destination_product.normalized_name or destination_product.product_name or ""
    top = index.search(query_text, top_k=1)
    if top and top[0].final_score >= high_threshold:
        master_product = db.get(MasterProduct, top[0].master_product_id)
        if master_product is not None:
            return auto_accept_match(
                db, destination_product, master_product, confidence=top[0].final_score, method="auto_threshold"
            )

    if low_threshold is not None:
        top_score = top[0].final_score if top else None
        if top_score is None or top_score < low_threshold:
            auto_reject_match(db, destination_product, top_score)

    return None


@dataclass
class AutoMatchResult:
    checked: int
    auto_matched: int
    exact_matches: int
    threshold_matches: int
    auto_rejected: int
    still_pending: int


def run_auto_match_batch(
    db: Session,
    index: CatalogSearchIndex,
    upload_id: str,
    high_threshold: float,
    low_threshold: float | None = None,
) -> AutoMatchResult:
    """Explicit, opt-in batch pass over every pending destination product
    in this upload (spec: "only enable after evaluation" - never runs
    silently on ingest/reindex). `low_threshold=None` (the default)
    disables auto-rejection entirely - every non-auto-accepted item stays
    pending for human review, exactly as before this feature existed.
    """
    pending = (
        db.query(DestinationProduct)
        .filter(DestinationProduct.upload_id == upload_id, DestinationProduct.status == "pending")
        .all()
    )

    checked = len(pending)
    exact_matches = 0
    threshold_matches = 0
    auto_rejected = 0

    for dp in pending:
        exact = find_exact_match(db, dp)
        if exact is not None:
            auto_accept_match(db, dp, exact, confidence=0.99, method="exact_match")
            exact_matches += 1
            continue

        query_text = dp.normalized_name or dp.product_name or ""
        top = index.search(query_text, top_k=1)
        if top and top[0].final_score >= high_threshold:
            master_product = db.get(MasterProduct, top[0].master_product_id)
            if master_product is not None:
                auto_accept_match(db, dp, master_product, confidence=top[0].final_score, method="auto_threshold")
                threshold_matches += 1
                continue

        if low_threshold is not None:
            top_score = top[0].final_score if top else None
            if top_score is None or top_score < low_threshold:
                auto_reject_match(db, dp, top_score)
                auto_rejected += 1

    auto_matched = exact_matches + threshold_matches
    return AutoMatchResult(
        checked=checked,
        auto_matched=auto_matched,
        exact_matches=exact_matches,
        threshold_matches=threshold_matches,
        auto_rejected=auto_rejected,
        still_pending=checked - auto_matched - auto_rejected,
    )


# --- Phase 8: active learning (uncertainty-based review prioritization) ---


def compute_uncertainty_margin(index: CatalogSearchIndex, destination_product: DestinationProduct) -> float | None:
    """Gap between the top-2 reranked candidates' scores (spec section 24's
    own example: 0.51 vs 0.49 = highly uncertain; 0.99 vs 0.32 = confident).
    None if there are fewer than 2 candidates - nothing to be uncertain
    between.
    """
    query_text = destination_product.normalized_name or destination_product.product_name or ""
    top2 = index.search_reranked(query_text, top_k=2)
    if len(top2) < 2:
        return None
    return top2[0].reranker_score - top2[1].reranker_score


@dataclass
class PrioritizeResult:
    checked: int
    computed: int
    insufficient_candidates: int


def prioritize_batch(db: Session, index: CatalogSearchIndex, upload_id: str) -> PrioritizeResult:
    """Explicit, opt-in batch pass (same pattern as Phase 5's
    /auto-match): compute and store `uncertainty_margin` for every
    currently-pending destination product in this upload, so
    `get_next_pending(..., strategy="uncertainty")` can order the review
    queue without recomputing search results on every request.
    """
    pending = (
        db.query(DestinationProduct)
        .filter(DestinationProduct.upload_id == upload_id, DestinationProduct.status == "pending")
        .all()
    )

    computed = 0
    insufficient = 0
    for dp in pending:
        margin = compute_uncertainty_margin(index, dp)
        if margin is None:
            insufficient += 1
            continue
        dp.uncertainty_margin = margin
        computed += 1

    db.flush()
    return PrioritizeResult(checked=len(pending), computed=computed, insufficient_candidates=insufficient)
