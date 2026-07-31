"""Smoke tests for growing multi-catalog DB: upsert, skip-source, custom fields."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.db_models import (
    CatalogFieldDef,
    CatalogProduct,
    CatalogSource,
)
from app.services.catalog_import import import_catalog_rows
from app.services.catalog_schema import ensure_source_schema
from app.services.matching_job import resolve_match_sources, _merge_candidates
from app.matching.base import Candidate
from app.schemas import RunMatchingRequest


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_ensure_schema_seeds_fields_and_version():
    db = _session()
    source = CatalogSource(name="government", kind="government", is_enabled=True)
    db.add(source)
    db.commit()

    version = ensure_source_schema(db, source)
    assert version.is_current
    fields = db.query(CatalogFieldDef).filter(CatalogFieldDef.source_id == source.id).all()
    assert any(f.key == "name" and f.is_core for f in fields)
    assert any(f.key == "technical_specs" for f in fields)
    db.close()


def test_upsert_updates_existing_and_inserts_new():
    db = _session()
    source = CatalogSource(name="government", kind="government", is_enabled=True)
    db.add(source)
    db.commit()
    ensure_source_schema(db, source)

    rows1 = [
        {
            "code": "521-001",
            "name": "Стол ученический",
            "brand": None,
            "model": None,
            "description": "v1",
            "technical_specs": "дерево",
            "price": 100.0,
            "category_code": "521",
            "category_name": "Мебель",
            "normalized_text": "521-001 стол ученический дерево",
            "custom_fields": {"unit": "шт"},
        }
    ]
    stats1 = import_catalog_rows(db, source, rows1, mode="upsert", version_label="2026-Q1")
    assert stats1["inserted"] == 1
    assert stats1["updated"] == 0

    rows2 = [
        {
            "code": "521-001",
            "name": "Стол ученический",
            "brand": None,
            "model": None,
            "description": "v2 updated",
            "technical_specs": "ЛДСП",
            "price": 120.0,
            "category_code": "521",
            "category_name": "Мебель",
            "normalized_text": "521-001 стол ученический лдсп",
            "custom_fields": {"unit": "шт"},
        },
        {
            "code": "521-002",
            "name": "Стул",
            "brand": None,
            "model": None,
            "description": "new",
            "technical_specs": None,
            "price": 50.0,
            "category_code": "521",
            "category_name": "Мебель",
            "normalized_text": "521-002 стул",
            "custom_fields": {},
        },
    ]
    stats2 = import_catalog_rows(db, source, rows2, mode="upsert", version_label="2026-Q1")
    assert stats2["inserted"] == 1
    assert stats2["updated"] == 1

    products = db.query(CatalogProduct).filter(CatalogProduct.source_id == source.id).all()
    assert len(products) == 2
    updated = next(p for p in products if p.code == "521-001")
    assert updated.price == 120.0
    assert updated.description == "v2 updated"
    assert (updated.custom_fields or {}).get("unit") == "шт"
    db.close()


def test_custom_field_def_persists():
    db = _session()
    source = CatalogSource(name="supplier_acme", kind="supplier", is_enabled=True)
    db.add(source)
    db.commit()
    ensure_source_schema(db, source)

    db.add(CatalogFieldDef(
        source_id=source.id,
        key="warranty_months",
        label="Гарантия (мес)",
        field_type="number",
        is_core=False,
        sort_order=200,
        show_in_table=True,
        use_in_matching=False,
    ))
    db.commit()

    keys = {f.key for f in db.query(CatalogFieldDef).filter(CatalogFieldDef.source_id == source.id)}
    assert "warranty_months" in keys
    assert "name" in keys
    db.close()


def test_resolve_match_sources_skips_disabled():
    db = _session()
    gov = CatalogSource(name="government", kind="government", is_enabled=True)
    sup = CatalogSource(name="supplier_x", kind="supplier", is_enabled=False)
    db.add_all([gov, sup])
    db.commit()

    payload = RunMatchingRequest(source_ids=[gov.id, sup.id])
    resolved = resolve_match_sources(db, payload)
    assert [s.name for s in resolved] == ["government"]
    db.close()


def test_merge_candidates_keeps_best_score_per_product():
    a = Candidate(catalog_product_id=1, score=0.5, explanation="a")
    b = Candidate(catalog_product_id=1, score=0.9, explanation="b")
    c = Candidate(catalog_product_id=2, score=0.7, explanation="c")
    merged = _merge_candidates([[a], [b, c]], top_n=2)
    assert len(merged) == 2
    assert merged[0].catalog_product_id == 1
    assert merged[0].score == 0.9
    assert merged[1].catalog_product_id == 2


def test_deactivate_missing_on_upsert():
    db = _session()
    source = CatalogSource(name="government", kind="government", is_enabled=True)
    db.add(source)
    db.commit()
    ensure_source_schema(db, source)

    import_catalog_rows(db, source, [
        {
            "code": "A", "name": "Keep", "brand": None, "model": None,
            "description": None, "technical_specs": None, "price": 1,
            "category_code": None, "category_name": None,
            "normalized_text": "a keep", "custom_fields": {},
        },
        {
            "code": "B", "name": "Drop", "brand": None, "model": None,
            "description": None, "technical_specs": None, "price": 1,
            "category_code": None, "category_name": None,
            "normalized_text": "b drop", "custom_fields": {},
        },
    ], mode="upsert")

    stats = import_catalog_rows(db, source, [
        {
            "code": "A", "name": "Keep", "brand": None, "model": None,
            "description": None, "technical_specs": None, "price": 2,
            "category_code": None, "category_name": None,
            "normalized_text": "a keep", "custom_fields": {},
        },
    ], mode="upsert", deactivate_missing=True)

    assert stats["deactivated"] == 1
    drop = db.query(CatalogProduct).filter(CatalogProduct.code == "B").first()
    assert drop.is_active is False
    db.close()
