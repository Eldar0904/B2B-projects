"""Tests for catalog re-upload stale match cleanup."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.api.routes import _clear_matches_for_catalog_source
from app.models.db_models import (
    CatalogSource, CatalogProduct, InternalItem, MatchResult,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_clear_matches_for_catalog_source_removes_stale_rows():
    db = _session()
    source = CatalogSource(name="government")
    db.add(source)
    db.commit()

    p1 = CatalogProduct(source_id=source.id, code="A", name="Product A", normalized_text="a")
    p2 = CatalogProduct(source_id=source.id, code="B", name="Product B", normalized_text="b")
    db.add_all([p1, p2])
    db.commit()

    item = InternalItem(item_code="X", item_name="Item X", normalized_text="x")
    db.add(item)
    db.commit()

    db.add(MatchResult(
        item_id=item.id, catalog_product_id=p1.id,
        rank=1, confidence_score=0.9, explanation="test", is_selected=1,
    ))
    db.commit()

    removed = _clear_matches_for_catalog_source(db, source.id)
    assert removed == 1
    assert db.query(MatchResult).count() == 0
    db.close()


def test_clear_matches_does_not_touch_other_sources():
    db = _session()
    gov = CatalogSource(name="government")
    other = CatalogSource(name="supplier")
    db.add_all([gov, other])
    db.commit()

    gov_product = CatalogProduct(source_id=gov.id, code="G1", name="Gov", normalized_text="g")
    other_product = CatalogProduct(source_id=other.id, code="S1", name="Sup", normalized_text="s")
    db.add_all([gov_product, other_product])
    db.commit()

    item = InternalItem(item_code="X", item_name="Item", normalized_text="x")
    db.add(item)
    db.commit()

    db.add(MatchResult(
        item_id=item.id, catalog_product_id=other_product.id,
        rank=1, confidence_score=0.8, explanation="other", is_selected=1,
    ))
    db.commit()

    removed = _clear_matches_for_catalog_source(db, gov.id)
    assert removed == 0
    assert db.query(MatchResult).count() == 1
    db.close()
