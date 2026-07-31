import os
import shutil
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, BackgroundTasks, Depends, UploadFile, File, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.config import settings
from app.models.db_models import (
    CatalogSource, CatalogProduct, CatalogVersion, CatalogFieldDef,
    CatalogImportMapping, InternalItem, MatchResult, MatchingRun,
    Project, ProjectCatalogLink,
)
from app.schemas import (
    UploadResponse, RunMatchingRequest, SelectMatchRequest,
    InternalItemOut, CatalogSourceOut, CatalogSourceCreate, CatalogSourceUpdate,
    CatalogVersionOut, CatalogVersionCreate,
    CatalogProductOut, CatalogProductCreate, CatalogProductUpdate,
    CatalogFieldDefOut, CatalogFieldDefCreate, CatalogFieldDefUpdate, FieldDefsReorderRequest,
    ImportMappingOut, ImportMappingPut,
    CategoryOut, ProjectOut, ProjectCreate, ProjectUpdate, ProjectCatalogLinksPut,
    ProjectCatalogLinkOut,
)
from app.services.excel_import import peek_excel_headers, read_items_excel
from app.services.catalog_import import (
    clear_matches_for_catalog_source,
    load_catalog_from_excel,
)
from app.services.catalog_schema import ensure_source_schema, get_or_create_version
from app.services.export import export_results, export_results_batched
from app.services.embedding import embed_catalog_source, embeddings_available
from app.services.category import backfill_source_categories
from app.services.matching_job import (
    execute_matching_run, reconcile_matching_runs, cancel_active_matching_run,
    resolve_match_sources,
)
from app.services.normalize import build_normalized_text

router = APIRouter()

# Re-export for existing tests
_clear_matches_for_catalog_source = clear_matches_for_catalog_source


def _get_or_create_source(
    db: Session,
    name: str,
    kind: str = "government",
) -> CatalogSource:
    source = db.query(CatalogSource).filter(CatalogSource.name == name).first()
    if not source:
        source = CatalogSource(
            name=name,
            description=f"Catalog: {name}",
            kind=kind if name != "government" else "government",
            is_enabled=True,
            is_archived=False,
        )
        db.add(source)
        db.commit()
        db.refresh(source)
    ensure_source_schema(db, source)
    return source


def _source_out(db: Session, s: CatalogSource) -> CatalogSourceOut:
    current = (
        db.query(CatalogVersion)
        .filter(CatalogVersion.source_id == s.id, CatalogVersion.is_current.is_(True))
        .first()
    )
    count_q = db.query(CatalogProduct).filter(CatalogProduct.source_id == s.id)
    if current:
        count_q = count_q.filter(CatalogProduct.version_id == current.id)
    return CatalogSourceOut(
        id=s.id,
        name=s.name,
        description=s.description,
        kind=s.kind or "government",
        is_enabled=bool(s.is_enabled),
        is_archived=bool(s.is_archived),
        product_count=count_q.filter(CatalogProduct.is_active.is_(True)).count(),
        current_version_label=current.label if current else None,
    )


def _product_out(p: CatalogProduct, source_name: Optional[str] = None) -> CatalogProductOut:
    return CatalogProductOut(
        id=p.id,
        source_id=p.source_id,
        version_id=p.version_id,
        code=p.code,
        name=p.name,
        brand=p.brand,
        model=p.model,
        description=p.description,
        technical_specs=p.technical_specs,
        price=p.price,
        category_code=p.category_code,
        category_name=p.category_name,
        custom_fields=p.custom_fields or {},
        is_active=bool(p.is_active) if p.is_active is not None else True,
        source_name=source_name,
    )


# ── Sources ──────────────────────────────────────────────────────

@router.get("/catalog-sources", response_model=list[CatalogSourceOut])
def list_catalog_sources(
    include_archived: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    q = db.query(CatalogSource)
    if not include_archived:
        q = q.filter(CatalogSource.is_archived.is_(False))
    sources = q.order_by(CatalogSource.name.asc()).all()
    return [_source_out(db, s) for s in sources]


@router.post("/catalog-sources", response_model=CatalogSourceOut)
def create_catalog_source(payload: CatalogSourceCreate, db: Session = Depends(get_db)):
    existing = db.query(CatalogSource).filter(CatalogSource.name == payload.name).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Source '{payload.name}' already exists")
    source = CatalogSource(
        name=payload.name,
        description=payload.description,
        kind=payload.kind,
        is_enabled=payload.is_enabled,
        is_archived=False,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    ensure_source_schema(db, source)
    return _source_out(db, source)


@router.patch("/catalog-sources/{source_id}", response_model=CatalogSourceOut)
def patch_catalog_source(
    source_id: int,
    payload: CatalogSourceUpdate,
    db: Session = Depends(get_db),
):
    source = db.query(CatalogSource).filter(CatalogSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Catalog source not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(source, field, value)
    db.commit()
    db.refresh(source)
    return _source_out(db, source)


# ── Versions ─────────────────────────────────────────────────────

@router.get("/catalog-sources/{source_id}/versions", response_model=list[CatalogVersionOut])
def list_versions(source_id: int, db: Session = Depends(get_db)):
    source = db.query(CatalogSource).filter(CatalogSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Catalog source not found")
    versions = (
        db.query(CatalogVersion)
        .filter(CatalogVersion.source_id == source_id)
        .order_by(CatalogVersion.id.desc())
        .all()
    )
    out = []
    for v in versions:
        count = (
            db.query(CatalogProduct)
            .filter(CatalogProduct.version_id == v.id, CatalogProduct.is_active.is_(True))
            .count()
        )
        out.append(CatalogVersionOut(
            id=v.id, source_id=v.source_id, label=v.label,
            effective_from=v.effective_from, is_current=v.is_current,
            notes=v.notes, product_count=count,
        ))
    return out


@router.post("/catalog-sources/{source_id}/versions", response_model=CatalogVersionOut)
def create_version(
    source_id: int,
    payload: CatalogVersionCreate,
    db: Session = Depends(get_db),
):
    source = db.query(CatalogSource).filter(CatalogSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Catalog source not found")
    version = get_or_create_version(
        db, source, label=payload.label, set_current=payload.set_current,
    )
    if payload.notes:
        version.notes = payload.notes
        db.commit()
        db.refresh(version)
    return CatalogVersionOut(
        id=version.id, source_id=version.source_id, label=version.label,
        effective_from=version.effective_from, is_current=version.is_current,
        notes=version.notes, product_count=0,
    )


@router.post("/catalog-sources/{source_id}/versions/{version_id}/set-current")
def set_current_version(source_id: int, version_id: int, db: Session = Depends(get_db)):
    version = (
        db.query(CatalogVersion)
        .filter(CatalogVersion.id == version_id, CatalogVersion.source_id == source_id)
        .first()
    )
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")
    for v in db.query(CatalogVersion).filter(CatalogVersion.source_id == source_id).all():
        v.is_current = v.id == version_id
    db.commit()
    return {"message": "Current version updated", "version_id": version_id, "label": version.label}


# ── Field defs (table designer) ──────────────────────────────────

@router.get("/catalog-sources/{source_id}/fields", response_model=list[CatalogFieldDefOut])
def list_fields(source_id: int, db: Session = Depends(get_db)):
    source = db.query(CatalogSource).filter(CatalogSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Catalog source not found")
    ensure_source_schema(db, source)
    return (
        db.query(CatalogFieldDef)
        .filter(CatalogFieldDef.source_id == source_id)
        .order_by(CatalogFieldDef.sort_order.asc(), CatalogFieldDef.id.asc())
        .all()
    )


@router.post("/catalog-sources/{source_id}/fields", response_model=CatalogFieldDefOut)
def create_field(
    source_id: int,
    payload: CatalogFieldDefCreate,
    db: Session = Depends(get_db),
):
    source = db.query(CatalogSource).filter(CatalogSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Catalog source not found")
    key = payload.key.strip().lower().replace(" ", "_")
    exists = (
        db.query(CatalogFieldDef)
        .filter(CatalogFieldDef.source_id == source_id, CatalogFieldDef.key == key)
        .first()
    )
    if exists:
        raise HTTPException(status_code=409, detail=f"Field '{key}' already exists")
    fd = CatalogFieldDef(
        source_id=source_id,
        key=key,
        label=payload.label,
        field_type=payload.field_type,
        is_core=False,
        is_required=payload.is_required,
        sort_order=payload.sort_order,
        show_in_table=payload.show_in_table,
        use_in_matching=payload.use_in_matching,
    )
    db.add(fd)
    db.commit()
    db.refresh(fd)
    return fd


@router.patch("/catalog-sources/{source_id}/fields/{field_id}", response_model=CatalogFieldDefOut)
def patch_field(
    source_id: int,
    field_id: int,
    payload: CatalogFieldDefUpdate,
    db: Session = Depends(get_db),
):
    fd = (
        db.query(CatalogFieldDef)
        .filter(CatalogFieldDef.id == field_id, CatalogFieldDef.source_id == source_id)
        .first()
    )
    if not fd:
        raise HTTPException(status_code=404, detail="Field not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(fd, field, value)
    db.commit()
    db.refresh(fd)
    return fd


@router.delete("/catalog-sources/{source_id}/fields/{field_id}")
def delete_field(source_id: int, field_id: int, db: Session = Depends(get_db)):
    fd = (
        db.query(CatalogFieldDef)
        .filter(CatalogFieldDef.id == field_id, CatalogFieldDef.source_id == source_id)
        .first()
    )
    if not fd:
        raise HTTPException(status_code=404, detail="Field not found")
    if fd.is_core:
        raise HTTPException(status_code=400, detail="Core fields cannot be deleted")
    db.delete(fd)
    db.commit()
    return {"message": "Field deleted"}


@router.put("/catalog-sources/{source_id}/fields/reorder")
def reorder_fields(
    source_id: int,
    payload: FieldDefsReorderRequest,
    db: Session = Depends(get_db),
):
    fields = (
        db.query(CatalogFieldDef)
        .filter(CatalogFieldDef.source_id == source_id)
        .all()
    )
    by_key = {f.key: f for f in fields}
    for idx, key in enumerate(payload.ordered_keys):
        if key in by_key:
            by_key[key].sort_order = (idx + 1) * 10
    db.commit()
    return {"message": "Fields reordered"}


# ── Import mappings ──────────────────────────────────────────────

@router.get("/catalog-sources/{source_id}/import-mappings", response_model=list[ImportMappingOut])
def get_import_mappings(source_id: int, db: Session = Depends(get_db)):
    source = db.query(CatalogSource).filter(CatalogSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Catalog source not found")
    ensure_source_schema(db, source)
    return (
        db.query(CatalogImportMapping)
        .filter(CatalogImportMapping.source_id == source_id)
        .order_by(CatalogImportMapping.field_key.asc())
        .all()
    )


@router.put("/catalog-sources/{source_id}/import-mappings", response_model=list[ImportMappingOut])
def put_import_mappings(
    source_id: int,
    payload: ImportMappingPut,
    db: Session = Depends(get_db),
):
    source = db.query(CatalogSource).filter(CatalogSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Catalog source not found")
    db.query(CatalogImportMapping).filter(CatalogImportMapping.source_id == source_id).delete()
    for item in payload.mappings:
        header = item.excel_header.strip().lower()
        if not header:
            continue
        db.add(CatalogImportMapping(
            source_id=source_id,
            excel_header=header,
            field_key=item.field_key.strip(),
        ))
    db.commit()
    return get_import_mappings(source_id, db)


@router.post("/catalog-sources/{source_id}/import-mappings/preview-headers")
async def preview_import_headers(
    source_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    source = db.query(CatalogSource).filter(CatalogSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Catalog source not found")
    os.makedirs(settings.uploads_dir, exist_ok=True)
    dest = os.path.join(settings.uploads_dir, f"preview_{file.filename}")
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        headers = peek_excel_headers(dest)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"headers": headers}


# ── Products CRUD ────────────────────────────────────────────────

@router.get("/catalog-products", response_model=list[CatalogProductOut])
def list_products(
    source_id: Optional[int] = None,
    source_name: Optional[str] = None,
    version_id: Optional[int] = None,
    category_code: Optional[str] = None,
    q: Optional[str] = None,
    active_only: bool = True,
    limit: int = Query(default=100, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    query = db.query(CatalogProduct)
    source = None
    if source_id:
        source = db.query(CatalogSource).filter(CatalogSource.id == source_id).first()
    elif source_name:
        source = db.query(CatalogSource).filter(CatalogSource.name == source_name).first()
    if source:
        query = query.filter(CatalogProduct.source_id == source.id)
        if version_id:
            query = query.filter(CatalogProduct.version_id == version_id)
        else:
            current = (
                db.query(CatalogVersion)
                .filter(CatalogVersion.source_id == source.id, CatalogVersion.is_current.is_(True))
                .first()
            )
            if current:
                query = query.filter(CatalogProduct.version_id == current.id)
    if active_only:
        query = query.filter(CatalogProduct.is_active.is_(True))
    if category_code:
        query = query.filter(CatalogProduct.category_code == category_code)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(CatalogProduct.normalized_text.ilike(like))
    products = query.order_by(CatalogProduct.id.asc()).offset(offset).limit(limit).all()
    name = source.name if source else None
    return [_product_out(p, name) for p in products]


@router.get("/catalog-products/search")
def search_catalog_products(
    q: str,
    source_name: str = "government",
    source_ids: Optional[str] = Query(default=None, description="Comma-separated source ids"),
    limit: int = 20,
    db: Session = Depends(get_db),
):
    """Search products for manual override — optionally across multiple sources."""
    query = db.query(CatalogProduct).filter(CatalogProduct.is_active.is_(True))
    if source_ids:
        ids = [int(x) for x in source_ids.split(",") if x.strip().isdigit()]
        if ids:
            query = query.filter(CatalogProduct.source_id.in_(ids))
    else:
        source = db.query(CatalogSource).filter(CatalogSource.name == source_name).first()
        if not source:
            return []
        query = query.filter(CatalogProduct.source_id == source.id)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(CatalogProduct.normalized_text.ilike(like))
    products = query.limit(limit).all()
    source_names = {
        s.id: s.name
        for s in db.query(CatalogSource).filter(
            CatalogSource.id.in_({p.source_id for p in products} or {-1})
        ).all()
    }
    return [
        {
            "id": p.id, "code": p.code, "name": p.name, "brand": p.brand,
            "model": p.model, "price": p.price,
            "source_id": p.source_id,
            "source_name": source_names.get(p.source_id),
        }
        for p in products
    ]


@router.get("/catalog-products/{product_id}", response_model=CatalogProductOut)
def get_product(product_id: int, db: Session = Depends(get_db)):
    p = db.query(CatalogProduct).filter(CatalogProduct.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    source = db.query(CatalogSource).filter(CatalogSource.id == p.source_id).first()
    return _product_out(p, source.name if source else None)


@router.post("/catalog-sources/{source_id}/products", response_model=CatalogProductOut)
def create_product(
    source_id: int,
    payload: CatalogProductCreate,
    db: Session = Depends(get_db),
):
    source = db.query(CatalogSource).filter(CatalogSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Catalog source not found")
    version = None
    if payload.version_id:
        version = (
            db.query(CatalogVersion)
            .filter(CatalogVersion.id == payload.version_id, CatalogVersion.source_id == source_id)
            .first()
        )
    if not version:
        version = get_or_create_version(db, source)
    product = CatalogProduct(
        source_id=source_id,
        version_id=version.id,
        code=payload.code,
        name=payload.name,
        brand=payload.brand,
        model=payload.model,
        description=payload.description,
        technical_specs=payload.technical_specs,
        price=payload.price,
        category_code=payload.category_code,
        category_name=payload.category_name,
        custom_fields=payload.custom_fields or {},
        is_active=payload.is_active,
        normalized_text=build_normalized_text(
            payload.code, payload.name, payload.brand, payload.model,
            payload.description, payload.technical_specs,
        ),
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return _product_out(product, source.name)


@router.patch("/catalog-products/{product_id}", response_model=CatalogProductOut)
def patch_product(
    product_id: int,
    payload: CatalogProductUpdate,
    db: Session = Depends(get_db),
):
    product = db.query(CatalogProduct).filter(CatalogProduct.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(product, field, value)
    product.normalized_text = build_normalized_text(
        product.code, product.name, product.brand, product.model,
        product.description, product.technical_specs,
    )
    product.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(product)
    source = db.query(CatalogSource).filter(CatalogSource.id == product.source_id).first()
    return _product_out(product, source.name if source else None)


@router.delete("/catalog-products/{product_id}")
def soft_delete_product(product_id: int, db: Session = Depends(get_db)):
    product = db.query(CatalogProduct).filter(CatalogProduct.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    product.is_active = False
    product.updated_at = datetime.utcnow()
    db.commit()
    return {"message": "Product deactivated", "id": product_id}


# ── Upload ───────────────────────────────────────────────────────

@router.get("/match/capabilities")
def match_capabilities():
    """Tell the UI which matching features are available in this deployment."""
    return {
        "embeddings_available": embeddings_available(),
        "modes": [
            {
                "id": "fast",
                "label": "Быстрый",
                "description": "Код + TF-IDF (~3 мин на 3000 поз.)",
            },
            {
                "id": "balanced",
                "label": "Сбалансированный",
                "description": "Код + TF-IDF + fuzzy + семантика (рекомендуется)",
            },
            {
                "id": "semantic",
                "label": "Семантический",
                "description": "Как сбалансированный (+ LLM rerank в будущем)",
            },
        ],
        "default_mode": settings.default_matching_mode,
    }


@router.post("/upload/catalog", response_model=UploadResponse)
def upload_catalog(
    file: UploadFile = File(...),
    source_name: str = Query(default="government"),
    replace_existing: bool = Query(default=False),
    import_mode: str = Query(default="upsert"),
    version_label: Optional[str] = Query(default=None),
    deactivate_missing: bool = Query(default=False),
    compute_embeddings: bool = Query(default=None),
    kind: str = Query(default="government"),
    db: Session = Depends(get_db),
):
    """Upload a catalog Excel file. Default mode is upsert (non-destructive)."""
    os.makedirs(settings.uploads_dir, exist_ok=True)
    dest_path = os.path.join(settings.uploads_dir, file.filename)
    with open(dest_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    source = _get_or_create_source(db, source_name, kind=kind)
    mode = "replace" if replace_existing else import_mode
    if mode not in ("upsert", "replace"):
        raise HTTPException(status_code=400, detail="import_mode must be upsert or replace")

    try:
        rows, stats = load_catalog_from_excel(
            db,
            source,
            dest_path,
            mode=mode,
            version_label=version_label,
            deactivate_missing=deactivate_missing,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    categorized = backfill_source_categories(db, source.id)

    embedded = 0
    should_embed = (
        compute_embeddings
        if compute_embeddings is not None
        else settings.auto_embed_on_catalog_upload
    )
    if should_embed and embeddings_available():
        embedded = embed_catalog_source(db, source.id, force=True)

    msg = (
        f"Imported catalog '{source_name}' ({mode}): "
        f"+{stats['inserted']} / ~{stats['updated']} updated"
    )
    if stats.get("deactivated"):
        msg += f"; deactivated {stats['deactivated']}"
    if stats.get("cleared_matches"):
        msg += f"; cleared {stats['cleared_matches']} stale match(es)"
    if categorized:
        msg += f"; categorized {categorized} products"
    if embedded:
        msg += f"; embedded {embedded} products"

    return UploadResponse(
        message=msg,
        rows_imported=len(rows),
        inserted=stats.get("inserted"),
        updated=stats.get("updated"),
        deactivated=stats.get("deactivated"),
        version_id=stats.get("version_id"),
    )


@router.post("/upload/items", response_model=UploadResponse)
def upload_items(
    file: UploadFile = File(...),
    replace_existing: bool = Query(default=True),
    project_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
):
    """Upload Our_Items.xlsx."""
    os.makedirs(settings.uploads_dir, exist_ok=True)
    dest_path = os.path.join(settings.uploads_dir, file.filename)
    with open(dest_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        rows = read_items_excel(dest_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if project_id:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

    if replace_existing:
        q = db.query(InternalItem)
        if project_id:
            q = q.filter(InternalItem.project_id == project_id)
        q.delete()
        db.commit()

    for row in rows:
        db.add(InternalItem(project_id=project_id, **row))
    db.commit()

    return UploadResponse(message="Imported internal items", rows_imported=len(rows))


@router.get("/catalog/categories", response_model=list[CategoryOut])
def list_catalog_categories(
    source_name: str = Query(default="government"),
    source_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """List distinct categories detected in a catalog source."""
    if source_id:
        source = db.query(CatalogSource).filter(CatalogSource.id == source_id).first()
    else:
        source = db.query(CatalogSource).filter(CatalogSource.name == source_name).first()
    if not source:
        raise HTTPException(status_code=404, detail="Catalog source not found")

    backfill_source_categories(db, source.id)
    products = (
        db.query(CatalogProduct)
        .filter(CatalogProduct.source_id == source.id, CatalogProduct.is_active.is_(True))
        .all()
    )
    counts: dict[str, dict] = {}
    for p in products:
        if not p.category_code:
            continue
        entry = counts.setdefault(
            p.category_code,
            {"category_code": p.category_code, "category_name": p.category_name, "product_count": 0},
        )
        if p.category_name and not entry["category_name"]:
            entry["category_name"] = p.category_name
        entry["product_count"] += 1

    return sorted(counts.values(), key=lambda x: x["category_code"])


# ── Matching ─────────────────────────────────────────────────────

@router.post("/match/run")
def run_matching(
    payload: RunMatchingRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Start matching in the background. Supports multi-source selection."""
    sources = resolve_match_sources(db, payload)
    if not sources:
        raise HTTPException(
            status_code=404,
            detail="No enabled catalog sources selected (check source_ids / skip flags)",
        )

    items_q = db.query(InternalItem)
    if payload.project_id:
        items_q = items_q.filter(InternalItem.project_id == payload.project_id)
    items = items_q.all()
    if not items:
        items = db.query(InternalItem).all()
    if not items:
        raise HTTPException(status_code=400, detail="No internal items imported yet")

    reconcile_matching_runs(db)

    active = (
        db.query(MatchingRun)
        .filter(MatchingRun.finished_at.is_(None))
        .order_by(MatchingRun.id.desc())
        .first()
    )
    if active:
        raise HTTPException(
            status_code=409,
            detail=f"Matching already running (run #{active.id}, {active.items_processed} items done)",
        )

    from app.matching.matching_config import MatchingConfig
    match_cfg = MatchingConfig.from_request(
        matching_mode=payload.matching_mode,
        top_k_candidates=payload.top_k_candidates,
        top_n_results=payload.top_n_results,
        min_similarity_score=payload.min_similarity_score,
        use_code_matching=payload.use_code_matching,
        use_tfidf=payload.use_tfidf,
        use_fuzzy_text=payload.use_fuzzy_text,
        use_embeddings=payload.use_embeddings,
        embedding_model=payload.embedding_model,
        use_category_filter=payload.use_category_filter,
        infer_category_if_missing=payload.infer_category_if_missing,
    )

    params = match_cfg.to_dict()
    params["items_total"] = len(items)
    params["source_ids"] = [s.id for s in sources]
    params["source_names"] = [s.name for s in sources]
    if payload.project_id:
        params["project_id"] = payload.project_id

    run = MatchingRun(
        source_id=sources[0].id,
        engine_name=match_cfg.engine_name(),
        params=params,
        items_processed=0,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    background_tasks.add_task(execute_matching_run, run.id, payload)

    return {
        "message": "Matching started",
        "run_id": run.id,
        "items_total": len(items),
        "source_ids": [s.id for s in sources],
        "source_names": [s.name for s in sources],
        "status": "running",
    }


@router.get("/match/status")
def match_status(db: Session = Depends(get_db)):
    """Progress of the latest matching run."""
    reconcile_matching_runs(db)

    run = db.query(MatchingRun).order_by(MatchingRun.id.desc()).first()
    if not run:
        return {"status": "idle", "items_processed": 0, "items_total": 0}

    item_total = db.query(InternalItem).count()
    params = dict(run.params or {})
    total = int(params.get("items_total") or item_total)
    processed = run.items_processed or 0
    cancelled = bool(params.get("cancelled"))
    if cancelled:
        status = "cancelled"
    elif run.finished_at is None:
        status = "running"
    else:
        status = "complete"

    return {
        "run_id": run.id,
        "status": status,
        "items_processed": processed,
        "items_total": total,
        "engine_name": run.engine_name,
        "source_ids": params.get("source_ids"),
        "source_names": params.get("source_names"),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


@router.post("/match/cancel")
def cancel_matching(db: Session = Depends(get_db)):
    """Stop the active background matching run. Partial results are kept."""
    run = cancel_active_matching_run(db)
    if not run:
        return {"message": "No matching run in progress", "status": "idle"}
    total = int((run.params or {}).get("items_total") or db.query(InternalItem).count())
    return {
        "message": "Matching cancelled",
        "status": "cancelled",
        "run_id": run.id,
        "items_processed": run.items_processed or 0,
        "items_total": total,
    }


@router.get("/items", response_model=list[InternalItemOut])
def list_items(
    project_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    q = (
        db.query(InternalItem)
        .options(
            joinedload(InternalItem.matches)
            .joinedload(MatchResult.catalog_product)
            .joinedload(CatalogProduct.source)
        )
    )
    if project_id is not None:
        q = q.filter(InternalItem.project_id == project_id)
    items = q.all()
    result = []
    for item in items:
        matches_sorted = sorted(item.matches, key=lambda m: m.rank)
        result.append(InternalItemOut(
            id=item.id,
            item_code=item.item_code,
            item_name=item.item_name,
            description=item.description,
            quantity=item.quantity,
            project_id=item.project_id,
            category_code=item.category_code,
            category_name=item.category_name,
            matches=[
                {
                    "id": m.id,
                    "rank": m.rank,
                    "confidence_score": m.confidence_score,
                    "explanation": m.explanation,
                    "is_selected": bool(m.is_selected),
                    "is_manual_override": bool(m.is_manual_override),
                    "catalog_product": _product_out(
                        m.catalog_product,
                        m.catalog_product.source.name if m.catalog_product and m.catalog_product.source else None,
                    ),
                }
                for m in matches_sorted
            ],
        ))
    return result


@router.post("/match/select")
def select_match(payload: SelectMatchRequest, db: Session = Depends(get_db)):
    """User manually picks a final match for an item (from the top-N or overriding it)."""
    item = db.query(InternalItem).filter(InternalItem.id == payload.item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    db.query(MatchResult).filter(MatchResult.item_id == item.id).update({"is_selected": 0})

    match = (
        db.query(MatchResult)
        .filter(
            MatchResult.item_id == item.id,
            MatchResult.catalog_product_id == payload.catalog_product_id,
        )
        .first()
    )

    if match:
        match.is_selected = 1
        match.is_manual_override = 1
    else:
        product = db.query(CatalogProduct).filter(CatalogProduct.id == payload.catalog_product_id).first()
        if not product:
            raise HTTPException(status_code=404, detail="Catalog product not found")
        match = MatchResult(
            item_id=item.id,
            catalog_product_id=product.id,
            rank=0,
            confidence_score=1.0,
            explanation="Manually selected by user",
            is_selected=1,
            is_manual_override=1,
        )
        db.add(match)

    db.commit()
    return {"message": "Match updated"}


# ── Projects ─────────────────────────────────────────────────────

@router.get("/projects", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db)):
    projects = db.query(Project).order_by(Project.id.desc()).all()
    return [_project_out(db, p) for p in projects]


@router.post("/projects", response_model=ProjectOut)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)):
    project = Project(name=payload.name, code=payload.code, description=payload.description)
    db.add(project)
    db.flush()
    if payload.source_ids:
        for idx, sid in enumerate(payload.source_ids):
            db.add(ProjectCatalogLink(
                project_id=project.id,
                source_id=sid,
                include_in_matching=True,
                sort_order=idx * 10,
            ))
    db.commit()
    db.refresh(project)
    return _project_out(db, project)


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return _project_out(db, project)


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def patch_project(project_id: int, payload: ProjectUpdate, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    return _project_out(db, project)


@router.put("/projects/{project_id}/catalog-links", response_model=ProjectOut)
def put_project_catalog_links(
    project_id: int,
    payload: ProjectCatalogLinksPut,
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    db.query(ProjectCatalogLink).filter(ProjectCatalogLink.project_id == project_id).delete()
    for link in payload.links:
        db.add(ProjectCatalogLink(
            project_id=project_id,
            source_id=link.source_id,
            include_in_matching=link.include_in_matching,
            sort_order=link.sort_order,
        ))
    db.commit()
    db.refresh(project)
    return _project_out(db, project)


def _project_out(db: Session, project: Project) -> ProjectOut:
    links = (
        db.query(ProjectCatalogLink)
        .filter(ProjectCatalogLink.project_id == project.id)
        .order_by(ProjectCatalogLink.sort_order.asc())
        .all()
    )
    source_ids = [lnk.source_id for lnk in links]
    names = {}
    if source_ids:
        names = {
            s.id: s.name
            for s in db.query(CatalogSource).filter(CatalogSource.id.in_(source_ids)).all()
        }
    return ProjectOut(
        id=project.id,
        name=project.name,
        code=project.code,
        description=project.description,
        catalog_links=[
            ProjectCatalogLinkOut(
                id=lnk.id,
                source_id=lnk.source_id,
                source_name=names.get(lnk.source_id),
                include_in_matching=lnk.include_in_matching,
                sort_order=lnk.sort_order,
            )
            for lnk in links
        ],
    )


@router.post("/export")
def export(min_confidence: Optional[float] = Query(default=None, ge=0.0, le=1.0), db: Session = Depends(get_db)):
    filepath = export_results(db, min_confidence=min_confidence)
    return FileResponse(
        filepath,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=os.path.basename(filepath),
    )


@router.post("/export/batches")
def export_batches(
    min_confidence: Optional[float] = Query(default=0.8, ge=0.0, le=1.0),
    batch_size: int = Query(default=100, ge=1, le=5000),
    db: Session = Depends(get_db),
):
    filepath = export_results_batched(db, min_confidence=min_confidence, batch_size=batch_size)
    return FileResponse(
        filepath,
        media_type="application/zip",
        filename=os.path.basename(filepath),
    )
