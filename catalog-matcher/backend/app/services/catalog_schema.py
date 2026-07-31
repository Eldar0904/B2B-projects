"""
Default field definitions and import-mapping seeds for catalog sources.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.db_models import (
    CatalogFieldDef,
    CatalogImportMapping,
    CatalogSource,
    CatalogVersion,
)
from app.services.excel_import import CATALOG_HEADER_ALIASES

# Core product columns — non-deletable field defs seeded per source.
CORE_FIELD_DEFS: List[dict] = [
    {"key": "code", "label": "Код", "field_type": "string", "is_required": False, "sort_order": 10, "use_in_matching": True},
    {"key": "name", "label": "Наименование", "field_type": "string", "is_required": True, "sort_order": 20, "use_in_matching": True},
    {"key": "brand", "label": "Бренд", "field_type": "string", "is_required": False, "sort_order": 30, "use_in_matching": True},
    {"key": "model", "label": "Модель", "field_type": "string", "is_required": False, "sort_order": 40, "use_in_matching": True},
    {"key": "description", "label": "Описание", "field_type": "text", "is_required": False, "sort_order": 50, "use_in_matching": True},
    {"key": "technical_specs", "label": "Тех. характеристики", "field_type": "text", "is_required": False, "sort_order": 60, "use_in_matching": True},
    {"key": "price", "label": "Цена", "field_type": "number", "is_required": False, "sort_order": 70, "use_in_matching": False},
    {"key": "category", "label": "Категория", "field_type": "string", "is_required": False, "sort_order": 80, "use_in_matching": False},
    {"key": "category_code", "label": "Код категории", "field_type": "string", "is_required": False, "sort_order": 90, "use_in_matching": False},
]

CORE_PRODUCT_KEYS = {
    "code", "name", "brand", "model", "description", "technical_specs",
    "price", "category_code", "category_name", "normalized_text",
}


def ensure_source_schema(db: Session, source: CatalogSource) -> CatalogVersion:
    """Seed field defs, default import aliases, and a current version if missing."""
    existing_keys = {
        fd.key for fd in db.query(CatalogFieldDef).filter(CatalogFieldDef.source_id == source.id).all()
    }
    for spec in CORE_FIELD_DEFS:
        if spec["key"] in existing_keys:
            continue
        db.add(CatalogFieldDef(
            source_id=source.id,
            key=spec["key"],
            label=spec["label"],
            field_type=spec["field_type"],
            is_core=True,
            is_required=spec["is_required"],
            sort_order=spec["sort_order"],
            show_in_table=True,
            use_in_matching=spec["use_in_matching"],
        ))

    # Seed default Excel aliases as import mappings when source has none.
    mapping_count = (
        db.query(CatalogImportMapping)
        .filter(CatalogImportMapping.source_id == source.id)
        .count()
    )
    if mapping_count == 0:
        for field_key, aliases in CATALOG_HEADER_ALIASES.items():
            for alias in aliases:
                db.add(CatalogImportMapping(
                    source_id=source.id,
                    excel_header=alias,
                    field_key=field_key,
                ))

    version = (
        db.query(CatalogVersion)
        .filter(CatalogVersion.source_id == source.id, CatalogVersion.is_current.is_(True))
        .first()
    )
    if not version:
        version = (
            db.query(CatalogVersion)
            .filter(CatalogVersion.source_id == source.id)
            .order_by(CatalogVersion.id.asc())
            .first()
        )
    if not version:
        version = CatalogVersion(
            source_id=source.id,
            label="initial",
            effective_from=datetime.utcnow(),
            is_current=True,
            notes="Auto-created current version",
        )
        db.add(version)
        db.flush()
    elif not version.is_current:
        version.is_current = True

    db.commit()
    db.refresh(version)
    return version


def get_or_create_version(
    db: Session,
    source: CatalogSource,
    label: Optional[str] = None,
    set_current: bool = True,
) -> CatalogVersion:
    """Resolve version by label or return/create the current one."""
    ensure_source_schema(db, source)

    if label:
        version = (
            db.query(CatalogVersion)
            .filter(CatalogVersion.source_id == source.id, CatalogVersion.label == label)
            .first()
        )
        if not version:
            version = CatalogVersion(
                source_id=source.id,
                label=label,
                effective_from=datetime.utcnow(),
                is_current=False,
            )
            db.add(version)
            db.flush()
        if set_current:
            _set_current_version(db, source.id, version.id)
        db.commit()
        db.refresh(version)
        return version

    version = (
        db.query(CatalogVersion)
        .filter(CatalogVersion.source_id == source.id, CatalogVersion.is_current.is_(True))
        .first()
    )
    if version:
        return version
    return ensure_source_schema(db, source)


def _set_current_version(db: Session, source_id: int, version_id: int) -> None:
    versions = db.query(CatalogVersion).filter(CatalogVersion.source_id == source_id).all()
    for v in versions:
        v.is_current = v.id == version_id


def mapping_aliases_for_source(db: Session, source_id: int) -> Dict[str, List[str]]:
    """
    Build {field_key: [excel_header, ...]} from DB mappings.
    Falls back to built-in CATALOG_HEADER_ALIASES when empty.
    """
    rows = (
        db.query(CatalogImportMapping)
        .filter(CatalogImportMapping.source_id == source_id)
        .all()
    )
    if not rows:
        return {k: list(v) for k, v in CATALOG_HEADER_ALIASES.items()}

    out: Dict[str, List[str]] = {}
    for row in rows:
        out.setdefault(row.field_key, []).append(row.excel_header.strip().lower())
    # Ensure core aliases still present for robustness
    for key, aliases in CATALOG_HEADER_ALIASES.items():
        existing = {a.lower() for a in out.get(key, [])}
        for alias in aliases:
            if alias not in existing:
                out.setdefault(key, []).append(alias)
    return out
