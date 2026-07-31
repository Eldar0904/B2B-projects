from datetime import datetime

from pydantic import BaseModel, Field


class SheetInfo(BaseModel):
    name: str
    row_count: int
    detected_header_row: int
    columns: list[str]


class UploadStatus(BaseModel):
    id: str
    filename: str
    upload_type: str
    sheet_name: str | None
    status: str
    total_rows: int
    processed_rows: int
    skipped_rows: int
    error_report: list[dict] | None

    class Config:
        from_attributes = True


class UploadResult(BaseModel):
    upload: UploadStatus
    sheets: list[SheetInfo] | None = None


# --- Projects: one pinned master catalog + many destination files -------
#
# These are Pydantic models, not SQLAlchemy models. The distinction matters:
# SQLAlchemy models (app/models.py) describe database TABLES; Pydantic
# models describe the JSON SHAPE of a request or response. FastAPI uses
# them to validate incoming bodies, to serialize outgoing objects, and to
# generate the OpenAPI docs at /docs automatically.


class ProjectCreate(BaseModel):
    """Request body for POST /api/projects.

    FastAPI validates against this before the endpoint function runs, so an
    empty name is rejected with a 422 and a precise error message without a
    single line of hand-written checking.
    """

    name: str = Field(min_length=1, max_length=255, examples=["Детсад 2026"])
    description: str | None = Field(default=None, examples=["Апрельский каталог КазНИИСА"])


class ProjectRead(BaseModel):
    id: str
    name: str
    description: str | None
    master_upload_id: str | None
    created_at: datetime

    class Config:
        # Lets FastAPI build this straight from a SQLAlchemy Project object
        # by reading attributes, instead of requiring a dict.
        from_attributes = True


class ProjectUploadSummary(BaseModel):
    """One file inside a project, with enough counts to render a progress
    row without a follow-up request per file.
    """

    id: str
    filename: str
    upload_type: str
    sheet_name: str | None
    status: str
    product_count: int
    # None for master uploads - a catalog has no review progress.
    matched_count: int | None = None
    pending_count: int | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class ProjectDetail(ProjectRead):
    master: ProjectUploadSummary | None = None
    destinations: list[ProjectUploadSummary] = []


# --- Catalog management tab (HANDOFF.md section 13) -----------------------
#
# View/edit/(soft-)delete rows in the master catalog directly. See
# app/api/catalog.py's module docstring for the soft-delete reasoning.


class MasterProductRead(BaseModel):
    id: str
    upload_id: str
    source_row: int
    external_id: str | None
    product_name: str | None
    description: str | None
    unit: str | None
    price: float | None
    is_group_header: bool
    is_active: bool
    dim_w_mm: float | None
    dim_h_mm: float | None
    dim_d_mm: float | None
    material: str | None
    unit_normalized: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class MasterProductUpdate(BaseModel):
    """Request body for PATCH /api/catalog/products/{id}. Every field is
    optional so only the fields actually sent are changed - the endpoint
    reads `model_dump(exclude_unset=True)`, not this model's defaults, so a
    field that's simply absent from the request is left untouched rather
    than overwritten with None.

    Deliberately excludes `raw_data`, `upload_id`, `id`, `created_at`,
    `is_group_header`, and `normalized_name` - the ingested-file audit
    trail and structural fields aren't user-editable directly.
    `normalized_name` is derived FROM `product_name`/`description` at
    ingestion time (`normalizer.build_normalized_name`); the endpoint
    re-derives it automatically whenever either of those two fields is
    edited, so it can never silently drift out of sync with what search
    and exact-match actually compare against - see catalog.py's
    `update_product`.
    """

    external_id: str | None = None
    product_name: str | None = None
    description: str | None = None
    unit: str | None = None
    price: float | None = None
    material: str | None = None
    dim_w_mm: float | None = None
    dim_h_mm: float | None = None
    dim_d_mm: float | None = None
    unit_normalized: str | None = None


class MasterProductCreate(BaseModel):
    """Request body for POST /api/catalog/products - add a brand-new
    catalog row by hand, outside the normal Excel-upload ingestion path
    (NEXT_STEPS.md item 7: the Catalog tab previously had no way to do
    this at all).

    `product_name` is the only required field - a row with no name isn't
    a usable catalog entry (search, matching, and the Catalog tab's own
    list all key off it). Everything else mirrors `MasterProductUpdate`'s
    field set, minus `normalized_name` (derived server-side, same as a
    normal ingested row - see catalog.py's `create_product`) and
    structural fields (`upload_id`, `source_row`, `is_group_header`,
    `is_active`, `raw_data`) that aren't meaningful for a hand-entered row.
    """

    product_name: str
    external_id: str | None = None
    description: str | None = None
    unit: str | None = None
    price: float | None = None
    material: str | None = None
    dim_w_mm: float | None = None
    dim_h_mm: float | None = None
    dim_d_mm: float | None = None
    unit_normalized: str | None = None


class MasterProductListResponse(BaseModel):
    items: list[MasterProductRead]
    total: int
    limit: int
    offset: int
    # None when no CatalogVersion is marked active (e.g. legacy data from
    # before HANDOFF.md section 4 introduced CatalogVersion) - in that case
    # the list falls back to every MasterProduct row across every upload,
    # which may include duplicates from old runs (see the
    # cleanup_orphaned_master_products.py note in HANDOFF.md section 4).
    catalog_version_id: str | None
    catalog_version_name: str | None


class CatalogMergeResult(BaseModel):
    """Response for POST /api/catalog/versions/{id}/update-from-file - see
    app/services/catalog_merge.py for what each count actually means.
    Returned instead of a bare 204 so a reviewer can see exactly what an
    upload changed without a follow-up query, same reasoning as
    MasterProductRead being returned from delete/restore.
    """

    catalog_version_id: str
    updated: int
    reactivated: int
    inserted: int
    unmatched_existing: int
    total_active_products: int
    errors: list[dict]
