# -*- coding: utf-8 -*-
"""Calibration benchmark for the attribute-comparison scoring signal
(NEXT_STEPS.md item 5, `app/services/search/attribute_score.py`) - the
SAME measure-before-trusting discipline as `calibrate_embedding_provider.py`,
applied to a signal that was already tried once and made things WORSE
(HANDOFF.md section 5: top-1 accuracy fell monotonically from 80.0% at
weight 0 to 65.7% at weight 0.20 on the real files).

Run inside the container:

    docker compose exec backend python -m scripts.calibrate_attribute_score

Reports ground-truth top-1 accuracy AND score separation at several
candidate blend weights (0.0, 0.05, 0.10, 0.15, 0.20, 0.30) - weight 0.0 is
this run's own unmodified baseline (attribute_score.py has zero effect at
this weight; `_rescored_top1` special-cases it to return
`candidates[0]` directly, the real, unmodified top-1 from
`matching.get_top_candidates`). This script also force-disables the LLM
reranker tie-breaker (`ENABLE_LLM_RERANKER_FOR_HARD_CASES`) for its whole
run, restored in `finally` - that tie-breaker makes a live, non-deterministic
Gemini call, so leaving it on made two separate runs of this exact script
disagree on weight 0.0's own baseline. With it forced off, this run is
internally deterministic and the weight-by-weight comparison below is
trustworthy - but weight 0.0 here is NOT guaranteed to exactly match
`calibrate_embedding_provider.py`'s own reported numbers, since that
script does not disable the tie-breaker. If every weight above 0.0 makes
accuracy WORSE, or narrows score separation, this rewrite failed exactly
like the first one and should stay unwired. If some weight genuinely
helps, that is the number worth actually adding to ScoringWeights - never
guessed at, always measured here first.

--- How this differs from a "does this pass" check ------------------------

This does NOT modify `ScoringWeights`/`compute_final_score` at all - it
re-ranks each ground-truth item's ALREADY-RETRIEVED top-20 candidate pool
(`matching.get_top_candidates`, unchanged) by blending in
`attribute_score.compute_attribute_score` at each weight, entirely inside
this script. Production scoring is completely untouched by running this;
see `calibrate_embedding_provider.py`'s own docstring for the identical
"why this is safe to run repeatedly" reasoning (never commits, isolates
Qdrant into a temporary collection) - this script follows it exactly.
"""

from __future__ import annotations

import argparse
import random
import statistics
from pathlib import Path

from app.config import settings
from app.database import SessionLocal
from app.models import DestinationProduct, MasterProduct
from app.services import matching
from app.services.ingestion import IngestionOptions, ingest_destination, ingest_master
from app.services.search.attribute_score import AttributeProfile, compute_attribute_score
from app.services.search.index_manager import CatalogSearchIndex
from app.services.search.loader import load_master_records

DEFAULT_MASTER = Path(__file__).resolve().parent.parent / "calibration_data" / "Казниса апрель.xlsx"
DEFAULT_DESTINATION = Path(__file__).resolve().parent.parent / "calibration_data" / "Детсад.xlsx"
CALIBRATION_COLLECTION = "calibration_attr_tmp"

# Same progression the original, deleted benchmark used (HANDOFF.md
# section 5), so a regression is directly comparable to that measurement.
CANDIDATE_WEIGHTS = [0.0, 0.05, 0.10, 0.15, 0.20, 0.30]

CANDIDATE_POOL_SIZE = 20  # wide enough that re-ranking could plausibly promote a correct item into 1st, not just reshuffle the existing top-3


def _profile(row: MasterProduct | DestinationProduct) -> AttributeProfile:
    return AttributeProfile(
        dim_w_mm=float(row.dim_w_mm) if row.dim_w_mm is not None else None,
        dim_h_mm=float(row.dim_h_mm) if row.dim_h_mm is not None else None,
        dim_d_mm=float(row.dim_d_mm) if row.dim_d_mm is not None else None,
        material=row.material,
    )


def _ground_truth(db, master_upload_id: str, destination_upload_id: str) -> dict[str, str]:
    """Same definition as calibrate_embedding_provider.py's own - kept in
    sync deliberately so results from the two scripts are comparable.
    """
    master_by_name: dict[str, str] = {}
    for row in (
        db.query(MasterProduct)
        .filter(MasterProduct.upload_id == master_upload_id, MasterProduct.is_group_header.is_(False))
        .all()
    ):
        if row.normalized_name:
            master_by_name.setdefault(row.normalized_name, row.id)

    truth: dict[str, str] = {}
    for dp in db.query(DestinationProduct).filter(DestinationProduct.upload_id == destination_upload_id).all():
        if dp.normalized_name and dp.normalized_name in master_by_name:
            truth[dp.id] = master_by_name[dp.normalized_name]
    return truth


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return float("nan")
    values = sorted(values)
    idx = min(len(values) - 1, max(0, round(pct / 100 * (len(values) - 1))))
    return values[idx]


def _rescored_top1(candidates, dest_profile: AttributeProfile, weight: float) -> tuple[str | None, float | None]:
    """Blends attribute_score into the candidate pool at `weight` and
    returns the (id, score) of whichever candidate comes out on top.

    TWO real bugs were caught building this function, both by the same
    sanity check ("weight 0.00 should exactly match
    calibrate_embedding_provider.py's own accuracy") - worth recording
    both, since the second one only surfaced after fixing the first:

    1st attempt blended with `final_score`, but never re-derived weight
    0.00 from the ALREADY-reranked pool `matching.get_top_candidates`
    returns (`search_reranked()`, Phase 6) - printed 85.3% instead of the
    real 100%.

    2nd attempt "fixed" that by blending with `reranker_score` instead,
    reasoning that's what the pool is actually sorted by - but RRF's
    `reranker_score` (reranking.py's RRFReranker) is a rank-FUSION score,
    not a `[0, 1]` confidence number (real run: values around 0.03-0.05,
    vs. `attribute_score`'s `[0, 1]` range) - blending two incompatible
    scales meant the attribute term numerically dominated at almost any
    weight, which is why accuracy got monotonically WORSE as weight rose
    in that run. That result said nothing real about whether the attribute
    idea helps - it was purely a units mismatch.

    Actual fix: weight 0.00 is handled as a special case - just return
    `candidates[0]` UNCHANGED (the pool's own real, already-correctly-
    reranked top-1), which by construction cannot drift from
    calibrate_embedding_provider.py's own baseline. Weight > 0 blends
    `final_score` (genuinely `[0, 1]`, the same scale `attribute_score`
    uses) across the SAME already-reranking-selected top-20 pool - a
    well-defined, honest question: "starting from the pool reranking
    already picked, does further nudging its internal order by attribute
    similarity help?" - not a claim about replacing RRF itself.
    """
    if not candidates:
        return None, None
    if weight == 0.0:
        top = candidates[0]
        return top.master_product.id, top.candidate.final_score

    rescored = []
    for c in candidates:
        attr = compute_attribute_score(dest_profile, _profile(c.master_product))
        blended = (1 - weight) * c.candidate.final_score + weight * attr
        rescored.append((c.master_product.id, blended))
    rescored.sort(key=lambda x: x[1], reverse=True)
    return rescored[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master-path", default=str(DEFAULT_MASTER))
    parser.add_argument("--destination-path", default=str(DEFAULT_DESTINATION))
    parser.add_argument("--sample-size", type=int, default=150, help="Non-match rows to sample for score separation.")
    args = parser.parse_args()

    if not Path(args.master_path).exists():
        raise SystemExit(
            f"Master file not found: {args.master_path}\n"
            "Copy the real catalog file into backend/calibration_data/ (see this "
            "script's own docstring), or pass --master-path explicitly."
        )
    if not Path(args.destination_path).exists():
        raise SystemExit(f"Destination file not found: {args.destination_path}")

    original_collection = settings.qdrant_collection_name
    original_llm_tiebreaker = settings.enable_llm_reranker_for_hard_cases
    db = SessionLocal()
    index = CatalogSearchIndex()
    try:
        # Isolate from the real "products" collection - see module docstring
        # and calibrate_embedding_provider.py's identical reasoning.
        settings.qdrant_collection_name = CALIBRATION_COLLECTION

        # Real finding from a real run: with the LLM tie-breaker on
        # (ENABLE_LLM_RERANKER_FOR_HARD_CASES=true, section 7), weight
        # 0.00's "baseline" made a real Gemini call as part of reranking -
        # an LLM's answer is not guaranteed identical between two separate
        # process runs, so weight 0.00 here could legitimately disagree
        # with a DIFFERENT script's DIFFERENT run (calibrate_embedding_
        # provider.py) even with zero bugs in either one. Forced off here
        # so this benchmark is fully deterministic and reproducible on its
        # own, and so a real network/quota hiccup can never silently skew
        # an accuracy measurement - restored in `finally` either way.
        settings.enable_llm_reranker_for_hard_cases = False

        print(f"Ingesting {args.master_path} ...")
        master_upload = ingest_master(db, args.master_path, Path(args.master_path).name, IngestionOptions())
        print(f"Ingesting {args.destination_path} ...")
        destination_upload = ingest_destination(db, args.destination_path, Path(args.destination_path).name, IngestionOptions())
        db.flush()

        records = load_master_records(db, upload_id=master_upload.id)
        print(f"Building index over {len(records)} master rows (embedding_provider={settings.embedding_provider})...")
        stats = index.build(records)
        print(f"Index built: {stats.indexed_records} indexed, embedding_dim={stats.embedding_dim}\n")

        truth = _ground_truth(db, master_upload.id, destination_upload.id)
        print(f"Ground truth: {len(truth)} destination rows have an exact catalog name match.\n")

        matched_dest_ids = set(truth.keys())
        other_ids = [
            dp.id
            for dp in db.query(DestinationProduct).filter(DestinationProduct.upload_id == destination_upload.id).all()
            if dp.id not in matched_dest_ids
        ]
        sample_ids = random.sample(other_ids, min(args.sample_size, len(other_ids)))

        # Fetch each ground-truth item's real candidate pool ONCE (the
        # expensive, unchanged part - real retrieval/reranking) - only the
        # cheap re-scoring/re-sorting below repeats per weight.
        truth_candidates = {}
        for dest_id in truth:
            dp = db.get(DestinationProduct, dest_id)
            truth_candidates[dest_id] = (dp, matching.get_top_candidates(db, index, dp, top_k=CANDIDATE_POOL_SIZE))

        sample_candidates = {}
        for dest_id in sample_ids:
            dp = db.get(DestinationProduct, dest_id)
            sample_candidates[dest_id] = (dp, matching.get_top_candidates(db, index, dp, top_k=1))

        print(f"{'weight':>8}  {'top-1 acc':>10}  {'true mean':>10}  {'true p10':>9}  {'sample mean':>12}  {'sample p90':>11}  {'gap (p10-p90)':>14}")
        for weight in CANDIDATE_WEIGHTS:
            hits = 0
            true_scores: list[float] = []
            for dest_id, (dp, candidates) in truth_candidates.items():
                top1_id, top1_score = _rescored_top1(candidates, _profile(dp), weight)
                if top1_id == truth[dest_id]:
                    hits += 1
                if top1_score is not None:
                    true_scores.append(top1_score)

            nonmatch_scores: list[float] = []
            for dest_id, (dp, candidates) in sample_candidates.items():
                _id, score = _rescored_top1(candidates, _profile(dp), weight)
                if score is not None:
                    nonmatch_scores.append(score)

            acc = 100 * hits / (len(truth) or 1)
            true_mean = statistics.mean(true_scores) if true_scores else float("nan")
            true_p10 = _percentile(true_scores, 10)
            sample_mean = statistics.mean(nonmatch_scores) if nonmatch_scores else float("nan")
            sample_p90 = _percentile(nonmatch_scores, 90)
            gap = true_p10 - sample_p90
            print(
                f"{weight:>8.2f}  {acc:>9.1f}%  {true_mean:>10.3f}  {true_p10:>9.3f}  "
                f"{sample_mean:>12.3f}  {sample_p90:>11.3f}  {gap:>14.3f}"
            )

        print(
            "\nweight 0.00 is this run's own unmodified baseline (the real "
            "candidates[0] from matching.get_top_candidates, with the LLM "
            "reranker tie-breaker forced off for this whole run - see this "
            "script's own comment on that override for why: it makes a real "
            "Gemini call, and an LLM's answer is not guaranteed identical "
            "between separate runs, so it is NOT safe to assume this exactly "
            "matches a DIFFERENT script's DIFFERENT run, like "
            "calibrate_embedding_provider.py's own reported numbers, unless "
            "that script also disables it). What IS safe to trust: the "
            "WITHIN-this-run comparison across weights below, since every row "
            "was computed from the exact same just-built index and the exact "
            "same already-retrieved candidate pools. If accuracy drops or the "
            "gap column shrinks at every weight above 0.00, this signal failed "
            "again and must NOT be wired into ScoringWeights - same verdict as "
            "the original, deleted attempt (HANDOFF.md section 5)."
        )

    finally:
        # Never leave calibration data in the real DB - see module docstring.
        db.rollback()
        db.close()
        try:
            from app.services.search.vector_search import build_qdrant_client

            qdrant_client = build_qdrant_client(settings.qdrant_url, settings.qdrant_local_path)
            if qdrant_client.collection_exists(CALIBRATION_COLLECTION):
                qdrant_client.delete_collection(CALIBRATION_COLLECTION)
        except Exception as exc:  # noqa: BLE001 - cleanup failure must not hide the benchmark result
            print(f"(warning: could not clean up temporary Qdrant collection {CALIBRATION_COLLECTION!r}: {exc})")
        settings.qdrant_collection_name = original_collection
        settings.enable_llm_reranker_for_hard_cases = original_llm_tiebreaker


if __name__ == "__main__":
    main()
