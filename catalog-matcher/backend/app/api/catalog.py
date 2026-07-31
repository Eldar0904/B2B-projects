"""Master catalog management API (HANDOFF.md section 13) - view, edit, and
(soft-)delete rows in the master catalog table directly, outside the
normal upload-a-file-and-ingest flow.

    GET    /api/catalog/products               paginated, searchable list
    POST   /api/catalog/products                add a new row by hand
    GET    /api/catalog/products/{id}           one row
    PATCH  /api/catalog/products/{id}           edit editable fields
    DELETE /api/catalog/products/{id}           soft delete (is_active=False)
    POST   /api/catalog/products/{id}/restore   undo a soft delete

Scoped to "the active catalog" - the most-recently-created CatalogVersion
with `is_active=True` (see CatalogVersion's own docstring in models.py for
why more than one can be active at once; ties are broken by newest
`created_at`, the same ordering `standalone_matching.list_catalog_versions`
already uses). If no CatalogVersion is active at all (e.g. legacy data
from before HANDOFF.md section 4), falls back to every MasterProduct row
across every upload rather than erroring - see `_active_catalog_version`.

--- Why soft delete, not a real DELETE -------------------------------------

`Match.master_product_id`, `MatchCandidate.master_product_id`, and
`Feedback.selected_master_product_id` all reference `master_products.id`
with no ON DELETE rule - a hard delete of a row still referenced by a
confirmed match would either orphan those rows or raise an integrity
error, depending on the database. Soft-deleting (`is_active=False`) keeps
every piece of match/audit history intact and pointing at a real row,
while hiding the row from search, matching, and this API's default list -
the same "removal should be a separate, explicit, reversible action"
principle `projects.py`'s own `delete_project` already follows.

--- Why the search index isn't rebuilt synchronously here ------------------

Editing or deleting a row makes the in-memory search index for this
catalog version stale, but rebuilding it takes several seconds on a large
catalog (~11s measured for 5,163 rows, HANDOFF.md section 6) - doing that
on every single edit would make the table feel unusably slow. Instead,
this only calls `invalidate_cached_index_for_version`, which
`standalone_matching.run_matching_job` already treats a cache miss on and
rebuilds from automatically on the next real matching run. The main
"Upload & Review" tab's own global index (a separate, catalog-version-
agnostic singleton - see `index_manager.py`) is deliberately NOT touched
here; it already has the exact same "stale until POST /api/search/reindex"
characteristic for every other kind of master-catalog change (e.g.
uploading a brand new master file doesn't auto-reindex it either), so this
doesn't introduce a new kind of staleness, just the same pre-existing one.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.api.uploads import _save_temp_file
from app.database import get_db
from app.models import CatalogVersion, MasterProduct
from app.schemas import (
    CatalogMergeResult,
    MasterProductCreate,
    MasterProductListResponse,
    MasterProductRead,
    MasterProductUpdate,
)
from app.services.catalog_merge import merge_master_file_into_version
from app.services.ingestion import IngestionOptions
from app.services.normalizer import build_normalized_name
from app.services.search.index_manager import invalidate_cached_index_for_version

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


def _active_catalog_version(db: Session) -> CatalogVersion | None:
    """The catalog this whole router manages. See `CatalogVersion`'s own
    docstring: `is_active` is deliberately not unique, so ties are broken
    by newest `created_at` - the same ordering
    `standalone_matching.list_catalog_versions` uses for its "most recent
    first" picker.
    """
    return (
        db.query(CatalogVersion)
        .filter(CatalogVersion.is_active.is_(True))
        .order_by(CatalogVersion.created_at.desc())
        .first()
    )


def _get_product_or_404(db: Session, product_id: str) -> MasterProduct:
    product = db.get(MasterProduct, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail=f"Master product {product_id} not found")
    return product


def _invalidate_index_for_product(db: Session, product: MasterProduct) -> None:
    """Best-effort: find the CatalogVersion this product's upload belongs
    to (if any) and drop its cached search index, so the next real
    matching run rebuilds from the edited data instead of serving stale
    candidates. A product from an upload that was never wrapped in a
    CatalogVersion (e.g. ingested through the older review-tab flow before
    HANDOFF.md section 4) has no per-version cache entry to invalidate -
    that flow uses the separate global singleton index instead, which this
    module deliberately does not touch (see this module's own docstring).
    """
    version = (
        db.query(CatalogVersion)
        .filter(CatalogVersion.source_upload_id == product.upload_id)
        .first()
    )
    if version is not None:
        invalidate_cached_index_for_version(version.id)


@router.get("/products", response_model=MasterProductListResponse)
def list_products(
    q: str | None = Query(default=None, description="Filter by product name or catalog code"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    include_inactive: bool = Query(default=False, description="Include soft-deleted rows"),
    db: Session = Depends(get_db),
) -> MasterProductListResponse:
    version = _active_catalog_version(db)
    query = db.query(MasterProduct)
    if version is not None:
        query = query.filter(MasterProduct.upload_id == version.source_upload_id)
    if not include_inactive:
        query = query.filter(MasterProduct.is_active.is_(True))
    if q:
        # .ilike() is a portable SQLAlchemy operator (native ILIKE on
        # PostgreSQL, lower()/LIKE on SQLite) - safe under both the real
        # deployment's Postgres and this test suite's SQLite.
        like = f"%{q}%"
        query = query.filter(
            or_(MasterProduct.product_name.ilike(like), MasterProduct.external_id.ilike(like))
        )

    total = query.count()
    items = (
        query.order_by(MasterProduct.source_row.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return MasterProductListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        catalog_version_id=version.id if version else None,
        catalog_version_name=version.name if version else None,
    )


@router.post("/products", response_model=MasterProductRead, status_code=201)
def create_product(payload: MasterProductCreate, db: Session = Depends(get_db)) -> MasterProduct:
    """Add a brand-new catalog row by hand (NEXT_STEPS.md item 7) - the one
    thing this router couldn't do before: every other endpoint here manages
    a row that came from an Excel upload, but sometimes a single item needs
    adding without re-uploading (or fabricating) a whole spreadsheet.

    Attaches to whichever CatalogVersion is currently active - the same
    scope `list_products` reads from - so the new row shows up in the
    Catalog tab and is reachable by search/matching immediately. 400s if
    there's no active catalog at all: a hand-added row has to belong to
    *some* catalog, and unlike editing/deleting an existing row (which
    always has a real upload_id to fall back to), there is nothing to
    attach a brand-new one to otherwise.

    `source_row` is set to one past the highest existing row in this
    upload, so the Catalog tab's default `source_row` ordering puts
    hand-added rows at the end rather than interleaving them at row 0.
    `raw_data` carries a `_manually_added` marker instead of a real
    ingested Excel row, for the same traceability reason every other row's
    `raw_data` preserves its original source.
    """
    version = _active_catalog_version(db)
    if version is None:
        raise HTTPException(
            status_code=400,
            detail="No active catalog to add a product to - upload a master catalog file first.",
        )

    next_source_row = (
        db.query(func.max(MasterProduct.source_row))
        .filter(MasterProduct.upload_id == version.source_upload_id)
        .scalar()
        or 0
    ) + 1

    product = MasterProduct(
        upload_id=version.source_upload_id,
        source_row=next_source_row,
        external_id=payload.external_id,
        product_name=payload.product_name,
        normalized_name=build_normalized_name(payload.product_name, payload.description),
        description=payload.description,
        unit=payload.unit,
        price=payload.price,
        material=payload.material,
        dim_w_mm=payload.dim_w_mm,
        dim_h_mm=payload.dim_h_mm,
        dim_d_mm=payload.dim_d_mm,
        unit_normalized=payload.unit_normalized,
        is_group_header=False,
        is_active=True,
        raw_data={"_manually_added": True},
    )
    db.add(product)

    # Real count, not +1 arithmetic - matches catalog_merge.py's own
    # "recompute, don't increment" convention, so this can never drift out
    # of sync with whatever else may have touched the row count. No manual
    # "+1 for the row just added" needed (an earlier version of this code
    # had one, and double-counted): Session.query(...).count() autoflushes
    # pending changes before running, so the `db.add(product)` above is
    # already visible to this count - confirmed by a real test failure
    # (5 instead of 4) before this fix.
    version.product_count = (
        db.query(MasterProduct)
        .filter(
            MasterProduct.upload_id == version.source_upload_id,
            MasterProduct.is_active.is_(True),
            MasterProduct.is_group_header.is_(False),
        )
        .count()
    )

    db.commit()
    db.refresh(product)
    invalidate_cached_index_for_version(version.id)
    return product


@router.get("/products/{product_id}", response_model=MasterProductRead)
def get_product(product_id: str, db: Session = Depends(get_db)) -> MasterProduct:
    return _get_product_or_404(db, product_id)


@router.patch("/products/{product_id}", response_model=MasterProductRead)
def update_product(
    product_id: str, payload: MasterProductUpdate, db: Session = Depends(get_db)
) -> MasterProduct:
    """Edits only the fields actually present in the request body
    (`exclude_unset=True` - see MasterProductUpdate's docstring for why
    that matters for a PATCH). If `product_name` or `description` changes,
    `normalized_name` is re-derived the same way ingestion.py computes it
    originally, so an edited row keeps matching/searching consistently
    with its new name instead of silently comparing against the old one.
    """
    product = _get_product_or_404(db, product_id)
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(product, field, value)

    if "product_name" in changes or "description" in changes:
        product.normalized_name = build_normalized_name(product.product_name, product.description)

    db.commit()
    db.refresh(product)
    if changes:
        _invalidate_index_for_product(db, product)
    return product


@router.delete("/products/{product_id}", response_model=MasterProductRead)
def delete_product(product_id: str, db: Session = Depends(get_db)) -> MasterProduct:
    """Soft delete - see this module's own docstring for why. Returns the
    updated row (`is_active` now False) rather than a bare 204, so the
    frontend can show an undo affordance without a follow-up GET.
    """
    product = _get_product_or_404(db, product_id)
    if not product.is_active:
        return product  # already inactive - idempotent, not an error
    product.is_active = False
    db.commit()
    db.refresh(product)
    _invalidate_index_for_product(db, product)
    return product


@router.post("/products/{product_id}/restore", response_model=MasterProductRead)
def restore_product(product_id: str, db: Session = Depends(get_db)) -> MasterProduct:
    """Undo a soft delete."""
    product = _get_product_or_404(db, product_id)
    if product.is_active:
        return product  # already active - idempotent
    product.is_active = True
    db.commit()
    db.refresh(product)
    _invalidate_index_for_product(db, product)
    return product


# --- Incremental catalog refresh (NEXT_STEPS.md "April -> May") -------------


@router.post("/versions/{catalog_version_id}/update-from-file", response_model=CatalogMergeResult)
def update_catalog_from_file(
    catalog_version_id: str,
    file: UploadFile,
    sheet_name: str | None = None,
    db: Session = Depends(get_db),
) -> CatalogMergeResult:
    """Upload a newer master file (e.g. May's revision of the same КазНИИСА
    catalog April was ingested from) and merge it into `catalog_version_id`
    IN PLACE, instead of creating a brand new, parallel CatalogVersion.

    See app/services/catalog_merge.py's module docstring for the full
    reasoning. In short: rows are matched to the existing catalog by
    `external_id` (falling back to `normalized_name`), matched rows are
    updated on their EXISTING row (so Match/Feedback history stays valid),
    genuinely new codes are added, and anything in the old catalog that is
    simply absent from the new file is left completely untouched - this
    endpoint can only add or update, never remove.

    404s if `catalog_version_id` doesn't exist - unlike the rest of this
    router, which is scoped to "whichever version is active" automatically,
    this is a deliberately explicit, one-off action naming a specific
    version, the same way DELETE /products/{id} names a specific row rather
    than acting on "whatever is currently showing".
    """
    version = db.get(CatalogVersion, catalog_version_id)
    if version is None:
        raise HTTPException(status_code=404, detail=f"Catalog version {catalog_version_id} not found")

    tmp_path = _save_temp_file(file)
    try:
        options = IngestionOptions(sheet_name=sheet_name)
        stats = merge_master_file_into_version(db, version, str(tmp_path), file.filename, options)
    finally:
        tmp_path.unlink(missing_ok=True)

    return CatalogMergeResult(
        catalog_version_id=version.id,
        updated=stats.updated,
        reactivated=stats.reactivated,
        inserted=stats.inserted,
        unmatched_existing=stats.unmatched_existing,
        total_active_products=version.product_count,
        errors=stats.errors,
    )
