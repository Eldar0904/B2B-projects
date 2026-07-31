"""Phase 7 training dataset generation from stored Feedback (spec section
22): positive pairs from confirmed matches, hard negatives from every
other candidate that was shown but not selected.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import DestinationProduct, Feedback, MasterProduct
from app.services.ml.features import FeatureVector, compute_features
from app.services.search.index_manager import CatalogSearchIndex

POSITIVE_DECISION_TYPES = {"user_selected", "manual_search_selected", "auto_accepted"}


@dataclass
class TrainingPair:
    destination_product_id: str
    master_product_id: str
    label: int  # 1 = confirmed match, 0 = shown-but-not-selected / no_match
    features: FeatureVector
    source_decision_type: str


def _candidate_subscores(index: CatalogSearchIndex, query_text: str) -> dict[str, object]:
    if not query_text.strip():
        return {}
    scored = index.search(query_text, top_k=50)
    return {c.master_product_id: c for c in scored}


def build_training_pairs(
    db: Session, index: CatalogSearchIndex, upload_id: str | None = None
) -> list[TrainingPair]:
    """Reads every Feedback row (optionally scoped to one destination
    upload) and produces labeled (destination, master) pairs with
    features, per spec section 22.
    """
    query = db.query(Feedback)
    if upload_id is not None:
        query = query.join(
            DestinationProduct, Feedback.destination_product_id == DestinationProduct.id
        ).filter(DestinationProduct.upload_id == upload_id)

    pairs: list[TrainingPair] = []

    for feedback in query.all():
        destination_product = db.get(DestinationProduct, feedback.destination_product_id)
        if destination_product is None or not feedback.candidate_data:
            continue

        candidates_shown = feedback.candidate_data.get("candidates", [])
        if not candidates_shown:
            continue

        query_text = destination_product.normalized_name or destination_product.product_name or ""
        subscores = _candidate_subscores(index, query_text)

        for entry in candidates_shown:
            master_product_id = entry.get("id")
            if not master_product_id:
                continue
            master_product = db.get(MasterProduct, master_product_id)
            if master_product is None:
                continue

            label = 1 if master_product_id == feedback.selected_master_product_id else 0
            features = compute_features(destination_product, master_product, subscores.get(master_product_id))

            pairs.append(
                TrainingPair(
                    destination_product_id=destination_product.id,
                    master_product_id=master_product_id,
                    label=label,
                    features=features,
                    source_decision_type=feedback.decision_type,
                )
            )

    return pairs
