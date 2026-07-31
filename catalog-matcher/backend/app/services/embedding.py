"""
Semantic embeddings for catalog products.

Uses sentence-transformers when installed. Vectors are stored as JSON in the DB
(portable across SQLite and Postgres). On Postgres with pgvector, an optional
ANN index can be added later; search uses an in-memory matrix cache for now
(fast enough for ~5k products).
"""
from __future__ import annotations

import json
import logging
from typing import List, Optional, TYPE_CHECKING

import numpy as np
from sqlalchemy.orm import Session

from app.config import settings
from app.models.db_models import CatalogProduct

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

_model_cache: dict[str, "SentenceTransformer"] = {}


def embeddings_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        return False


def get_embedding_model(model_name: Optional[str] = None):
    name = model_name or settings.embedding_model
    if name not in _model_cache:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading embedding model: %s", name)
        _model_cache[name] = SentenceTransformer(name)
    return _model_cache[name]


def embedding_text_for_product(product: CatalogProduct) -> str:
    parts = [
        product.name,
        product.brand,
        product.model,
        product.description,
        product.technical_specs,
        product.code,
    ]
    return " ".join(p for p in parts if p).strip()


def encode_texts(texts: List[str], model_name: Optional[str] = None) -> np.ndarray:
    if not texts:
        return np.zeros((0, 0))
    model = get_embedding_model(model_name)
    vectors = model.encode(texts, batch_size=settings.embedding_batch_size, show_progress_bar=False)
    return np.asarray(vectors, dtype=np.float32)


def embed_catalog_source(
    db: Session,
    source_id: int,
    model_name: Optional[str] = None,
    force: bool = False,
) -> int:
    """
    Compute and persist embeddings for all products in a catalog source.
    Returns the number of products embedded.
    """
    if not embeddings_available():
        logger.warning("sentence-transformers not installed; skipping embeddings")
        return 0

    name = model_name or settings.embedding_model
    products = (
        db.query(CatalogProduct)
        .filter(CatalogProduct.source_id == source_id)
        .all()
    )
    if not products:
        return 0

    to_embed: List[CatalogProduct] = []
    texts: List[str] = []
    for p in products:
        if not force and p.embedding_json and p.embedding_model == name:
            continue
        text = embedding_text_for_product(p)
        if not text:
            continue
        to_embed.append(p)
        texts.append(text)

    if not texts:
        return 0

    vectors = encode_texts(texts, name)
    for product, vec in zip(to_embed, vectors):
        product.embedding_json = json.dumps(vec.tolist())
        product.embedding_model = name

    db.commit()
    return len(to_embed)


def parse_embedding(raw: Optional[str]) -> Optional[np.ndarray]:
    if not raw:
        return None
    try:
        return np.asarray(json.loads(raw), dtype=np.float32)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
