"""
Non-destructive catalog import (upsert) and replace helpers.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.db_models import CatalogProduct, CatalogSource, MatchResult
from app.services.catalog_schema import (
    get_or_create_version,
    mapping_aliases_for_source,
)
from app.services.excel_import import read_catalog_excel
from app.services.normalize import build_normalized_text


def _upsert_key(row: dict) -> Tuple:
    code = (row.get("code") or "").strip().lower()
    if code:
        return ("code", code)
    return (
        "nbm",
        (row.get("name") or "").strip().lower(),
        (row.get("brand") or "").strip().lower(),
        (row.get("model") or "").strip().lower(),
    )


def clear_matches_for_catalog_source(db: Session, source_id: int) -> int:
    product_ids = [
        pid for (pid,) in db.query(CatalogProduct.id)
        .filter(CatalogProduct.source_id == source_id)
        .all()
    ]
    if not product_ids:
        return 0
    return (
        db.query(MatchResult)
        .filter(MatchResult.catalog_product_id.in_(product_ids))
        .delete(synchronize_session=False)
    )


def import_catalog_rows(
    db: Session,
    source: CatalogSource,
    rows: List[dict],
    *,
    mode: str = "upsert",
    version_label: Optional[str] = None,
    deactivate_missing: bool = False,
) -> Dict[str, int]:
    """
    Import parsed catalog rows into a version.

    mode:
      - upsert (default): update by code / name+brand+model, insert new
      - replace: clear products for this source version (and matches), then insert
    """
    version = get_or_create_version(db, source, label=version_label, set_current=True)
    inserted = updated = deactivated = cleared_matches = 0

    if mode == "replace":
        cleared_matches = clear_matches_for_catalog_source(db, source.id)
        q = db.query(CatalogProduct).filter(
            CatalogProduct.source_id == source.id,
            CatalogProduct.version_id == version.id,
        )
        deleted = q.delete(synchronize_session=False)
        db.commit()
        for row in rows:
            db.add(_product_from_row(source.id, version.id, row))
            inserted += 1
        db.commit()
        return {
            "inserted": inserted,
            "updated": 0,
            "deactivated": deleted,
            "cleared_matches": cleared_matches,
            "version_id": version.id,
            "rows": len(rows),
        }

    existing = (
        db.query(CatalogProduct)
        .filter(
            CatalogProduct.source_id == source.id,
            CatalogProduct.version_id == version.id,
        )
        .all()
    )
    by_key: Dict[Tuple, CatalogProduct] = {}
    for p in existing:
        by_key[_upsert_key({
            "code": p.code,
            "name": p.name,
            "brand": p.brand,
            "model": p.model,
        })] = p

    seen_ids = set()
    for row in rows:
        key = _upsert_key(row)
        product = by_key.get(key)
        if product:
            _apply_row(product, row)
            product.is_active = True
            product.updated_at = datetime.utcnow()
            seen_ids.add(product.id)
            updated += 1
        else:
            product = _product_from_row(source.id, version.id, row)
            db.add(product)
            db.flush()
            by_key[key] = product
            seen_ids.add(product.id)
            inserted += 1

    if deactivate_missing:
        for p in existing:
            if p.id not in seen_ids and p.is_active:
                p.is_active = False
                p.updated_at = datetime.utcnow()
                deactivated += 1

    db.commit()
    return {
        "inserted": inserted,
        "updated": updated,
        "deactivated": deactivated,
        "cleared_matches": 0,
        "version_id": version.id,
        "rows": len(rows),
    }


def load_catalog_from_excel(
    db: Session,
    source: CatalogSource,
    file_path: str,
    **kwargs,
) -> Tuple[List[dict], Dict[str, int]]:
    """Read Excel using source mappings/custom fields, then import."""
    from app.models.db_models import CatalogFieldDef

    aliases = mapping_aliases_for_source(db, source.id)
    custom_keys = [
        fd.key
        for fd in db.query(CatalogFieldDef)
        .filter(CatalogFieldDef.source_id == source.id, CatalogFieldDef.is_core.is_(False))
        .all()
    ]
    # Allow custom keys in alias map
    for key in custom_keys:
        aliases.setdefault(key, [key.replace("_", " "), key])

    rows = read_catalog_excel(file_path, aliases=aliases, custom_field_keys=custom_keys)
    stats = import_catalog_rows(db, source, rows, **kwargs)
    return rows, stats


def _product_from_row(source_id: int, version_id: int, row: dict) -> CatalogProduct:
    custom = dict(row.get("custom_fields") or {})
    return CatalogProduct(
        source_id=source_id,
        version_id=version_id,
        code=row.get("code"),
        name=row.get("name"),
        brand=row.get("brand"),
        model=row.get("model"),
        description=row.get("description"),
        technical_specs=row.get("technical_specs"),
        price=row.get("price"),
        category_code=row.get("category_code"),
        category_name=row.get("category_name"),
        normalized_text=row.get("normalized_text") or build_normalized_text(
            row.get("code"), row.get("name"), row.get("brand"),
            row.get("model"), row.get("description"), row.get("technical_specs"),
        ),
        custom_fields=custom,
        is_active=True,
    )


def _apply_row(product: CatalogProduct, row: dict) -> None:
    product.code = row.get("code")
    product.name = row.get("name")
    product.brand = row.get("brand")
    product.model = row.get("model")
    product.description = row.get("description")
    product.technical_specs = row.get("technical_specs")
    product.price = row.get("price")
    product.category_code = row.get("category_code")
    product.category_name = row.get("category_name")
    product.normalized_text = row.get("normalized_text") or build_normalized_text(
        row.get("code"), row.get("name"), row.get("brand"),
        row.get("model"), row.get("description"), row.get("technical_specs"),
    )
    existing_custom = dict(product.custom_fields or {})
    existing_custom.update(row.get("custom_fields") or {})
    product.custom_fields = existing_custom
