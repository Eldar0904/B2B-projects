"""Feature extraction for the Phase 7 supervised matching model (spec
section 23).

Only computes the subset of spec section 23's feature list that this
project actually has data for - see ARCHITECTURE.md "Phase 7" for exactly
which features are missing and why (attribute extraction / category
classification were never built, and destination products never captured
a `unit` field). Nothing here fakes a feature as zero to pad out the
list; the feature vector is honestly smaller than the spec's.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models import DestinationProduct, MasterProduct
from app.services.search.types import ScoredCandidate

FEATURE_NAMES = [
    "embedding_score",
    "keyword_score",
    "fuzzy_name_score",
    "price_difference",
    "price_available",
]


@dataclass
class FeatureVector:
    embedding_score: float
    keyword_score: float
    fuzzy_name_score: float
    price_difference: float  # 1.0 (max) when price isn't available on both sides
    price_available: float  # 1.0 / 0.0 - lets the model learn to discount price_difference when this is 0

    def as_list(self) -> list[float]:
        return [
            self.embedding_score,
            self.keyword_score,
            self.fuzzy_name_score,
            self.price_difference,
            self.price_available,
        ]


def compute_price_difference(destination_price: float | None, master_price: float | None) -> tuple[float, float]:
    """Returns (price_difference, price_available). price_difference is
    normalized to [0, 1] via abs(a-b)/max(a,b); defaults to (1.0, 0.0)
    when either side is missing, since a real difference in price is
    strong negative evidence but we can't compute one at all here."""
    if destination_price is None or master_price is None:
        return 1.0, 0.0
    if destination_price <= 0 or master_price <= 0:
        return 1.0, 0.0
    diff = abs(float(destination_price) - float(master_price)) / max(float(destination_price), float(master_price))
    return diff, 1.0


def compute_features(
    destination_product: DestinationProduct,
    master_product: MasterProduct,
    candidate: ScoredCandidate | None,
) -> FeatureVector:
    """`candidate` is the freshly-recomputed ScoredCandidate for this
    (destination, master) pair, if the master product still appears in
    the destination product's current hybrid search results; None if it
    doesn't (e.g. the index changed since the feedback was recorded), in
    which case the retrieval sub-scores default to 0.0 - this pair simply
    carries less signal, which is honest rather than making something up.
    """
    price_difference, price_available = compute_price_difference(
        destination_product.price, master_product.price
    )
    return FeatureVector(
        embedding_score=candidate.embedding_score if candidate else 0.0,
        keyword_score=candidate.keyword_score if candidate else 0.0,
        fuzzy_name_score=candidate.fuzzy_name_score if candidate else 0.0,
        price_difference=price_difference,
        price_available=price_available,
    )
