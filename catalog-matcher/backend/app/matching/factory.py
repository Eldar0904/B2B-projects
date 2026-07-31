"""
Single place that wires up which retriever/filter/ranker implementation
is active. To move to embeddings+pgvector and an LLM ranker later, change
the imports/instantiation here only.
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.matching.base import MatchingEngine, BaseRetriever
from app.matching.tfidf_retriever import TfidfRetriever
from app.matching.code_retriever import CodeRetriever
from app.matching.fuzzy_retriever import FuzzyTextRetriever
from app.matching.embedding_retriever import EmbeddingRetriever
from app.matching.hybrid_retriever import HybridRetriever
from app.matching.deterministic_filter import DeterministicFilter
from app.matching.heuristic_ranker import HeuristicRanker
from app.matching.matching_config import MatchingConfig
from app.models.db_models import CatalogProduct, CatalogVersion
from app.services.embedding import embeddings_available
from app.services.category import is_category_header_code


def matchable_products(products: list[CatalogProduct]) -> list[CatalogProduct]:
    """Exclude section header rows — they are category titles, not products."""
    return [p for p in products if not is_category_header_code(p.code)]


def products_for_source(
    db: Session,
    source_id: int,
    version_id: Optional[int] = None,
    active_only: bool = True,
) -> list[CatalogProduct]:
    """Load products for matching — current version and active by default."""
    q = db.query(CatalogProduct).filter(CatalogProduct.source_id == source_id)
    if active_only:
        # Treat NULL as active for rows created before the column existed
        q = q.filter(
            (CatalogProduct.is_active.is_(True)) | (CatalogProduct.is_active.is_(None))
        )

    if version_id is not None:
        q = q.filter(CatalogProduct.version_id == version_id)
    else:
        current = (
            db.query(CatalogVersion)
            .filter(
                CatalogVersion.source_id == source_id,
                CatalogVersion.is_current.is_(True),
            )
            .first()
        )
        if current:
            q = q.filter(CatalogProduct.version_id == current.id)

    return q.all()


def build_engine(
    db: Session,
    source_id: int,
    config: Optional[MatchingConfig] = None,
    version_id: Optional[int] = None,
) -> MatchingEngine:
    cfg = config or MatchingConfig.from_request("balanced")

    products = products_for_source(db, source_id, version_id=version_id, active_only=True)
    matchable = matchable_products(products)
    matchable_ids = {p.id for p in matchable}
    product_lookup = {p.id: p for p in products}

    retrievers: list[BaseRetriever] = []

    if cfg.use_code_matching:
        retrievers.append(CodeRetriever(matchable, fuzzy_threshold=cfg.code_fuzzy_threshold))

    if cfg.use_tfidf:
        retrievers.append(TfidfRetriever(db, product_ids=matchable_ids))

    if cfg.use_fuzzy_text:
        retrievers.append(FuzzyTextRetriever(matchable, score_cutoff=cfg.fuzzy_text_threshold))

    if cfg.use_embeddings and embeddings_available():
        retrievers.append(
            EmbeddingRetriever(db, model_name=cfg.embedding_model, product_ids=matchable_ids)
        )

    if not retrievers:
        retrievers.append(TfidfRetriever(db, product_ids=matchable_ids))

    retriever: BaseRetriever = (
        retrievers[0] if len(retrievers) == 1 else HybridRetriever(retrievers)
    )

    filter_ = DeterministicFilter(
        product_lookup,
        min_score=cfg.min_similarity_score,
        use_category_filter=cfg.use_category_filter,
    )
    ranker = HeuristicRanker(product_lookup)

    return MatchingEngine(retriever=retriever, filter_=filter_, ranker=ranker)
