"""Incremental master catalog merge (upsert) - NEXT_STEPS.md's "April -> May"
item: a replacement catalog file that is mostly the same as the one already
ingested, with a handful of added, changed, or removed rows.

Why this exists
----------------
`ingest_master()` (ingestion.py) plus `_create_catalog_version()`
(standalone_matching.py) always treat a new master file as an entirely
unrelated catalog: a new Upload, a full new set of MasterProduct rows, a
new CatalogVersion. That is correct for "here is a different catalog", but
it is exactly the wrong behavior for "here is next month's near-identical
revision of the same catalog" - doing that every month reproduces
HANDOFF.md section 14.0's 15-duplicate-CatalogVersion bloat (67,522 +
10,388 stale rows cleaned up there, from the same "upload new" habit this
module exists to avoid), AND it silently orphans last month's
MasterProduct rows - which is worse, because `Match.master_product_id`,
`MatchCandidate.master_product_id`, and `Feedback.selected_master_product_id`
all point at a specific row's id, not just a product name (see
CatalogVersion's own docstring in models.py: that is deliberate, so a
confirmed match keeps meaning what a human actually approved). A new
CatalogVersion for the same real-world catalog would strand all of that
history pointing at rows nobody will ever query again.

What this does instead: match the new file's rows against the TARGET
CatalogVersion's existing rows by `external_id` (the "Код" column - for the
real КазНИИСА catalog this is confirmed unique: 5,026 of 5,026 real product
rows in "Казниса апрель.xlsx" have a distinct code, zero collisions),
falling back to `normalized_name` for the rare row with no code at all.
Matched rows are updated IN PLACE - same MasterProduct.id - so every
Match/MatchCandidate/Feedback row that already points at it keeps pointing
at something meaningful. Only genuinely new codes get a new MasterProduct
row.

Rows that existed before but are absent from the new file are left
completely untouched - not soft-deleted, not flagged, nothing. This is an
explicit, narrow additive merge, never a wholesale replace, per the actual
request this module was built for: a monthly refresh must not "completely
remove" what was there before. If a real removal is ever needed, that
should go through the existing, separate, explicit
DELETE /api/catalog/products/{id} path (app/api/catalog.py) - never as a
side effect of uploading a new file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import CatalogVersion, MasterProduct
from app.services.ingestion import IngestionOptions, ingest_master
from app.services.search.index_manager import invalidate_cached_index_for_version

# Fields copied from the newly-ingested "staging" row onto the existing,
# matched row. Deliberately excludes: id/upload_id/source_row/created_at
# (structural - the existing row's identity must never change); is_active
# (handled separately below - reactivation is a meaningful event worth its
# own counter, not a silent field copy); external_id (it IS the join key -
# if a file ever renumbers a code, that shows up as one row's data going
# untouched and a new row appearing, which is visible and auditable, rather
# than silently re-keying an existing row under a human's nose);
# updated_at (set explicitly to "now", not copied from the staging row,
# which has no updated_at of its own anyway - see MasterProduct's docstring).
_MERGE_FIELDS = (
    "product_name",
    "normalized_name",
    "description",
    "unit",
    "price",
    "freight_class",
    "gross_weight_kg",
    "is_group_header",
    "dim_w_mm",
    "dim_h_mm",
    "dim_d_mm",
    "material",
    "unit_normalized",
    "raw_data",
)


@dataclass
class CatalogMergeStats:
    """Summary returned to the API layer so a reviewer can see exactly what
    a merge did without opening the database - the same "make the effect of
    a destructive-looking action visible" instinct as app/api/catalog.py's
    delete/restore endpoints returning the updated row.
    """

    updated: int = 0             # existing rows whose data changed
    reactivated: int = 0         # subset of `updated` that had been soft-deleted
    inserted: int = 0            # genuinely new codes, never seen before
    unmatched_existing: int = 0  # rows already in the catalog, absent from
                                  # this file, left untouched (never deleted)
    errors: list[dict] = field(default_factory=list)
    staging_upload_id: str = ""


def merge_master_file_into_version(
    db: Session,
    catalog_version: CatalogVersion,
    path: str,
    filename: str,
    options: IngestionOptions | None = None,
) -> CatalogMergeStats:
    """Ingest `path` as a normal master file (reusing every existing rule in
    ingestion.py unchanged - column mapping, attribute extraction, per-row
    error handling, VARCHAR truncation), then fold the result into
    `catalog_version`'s existing MasterProduct rows instead of leaving it as
    a second, parallel copy of the catalog.

    Commits once, at the end. A half-merged catalog (some rows updated,
    others not, with no record of which) would be worse than this function
    raising outright and leaving the catalog exactly as it was before the
    attempt - the caller (the API layer) is expected to let a raised
    exception roll the whole transaction back.
    """
    options = options or IngestionOptions()

    # Step 1: ingest the new file exactly like any other master upload, into
    # a throwaway "staging" Upload. Never shown to a user as a real catalog
    # version - its MasterProduct rows either get merged into an existing
    # row and deleted (matched case) or get re-parented onto the target
    # catalog's own upload_id (new-code case) below, so nothing is ever left
    # permanently attached to this staging upload.
    staging_upload = ingest_master(db, path, filename, options)
    db.flush()

    target_upload_id = catalog_version.source_upload_id

    # Every row already in the target catalog, INCLUDING soft-deleted ones -
    # see the module docstring: if the new file still lists a code that was
    # previously (soft-)deleted, the file is the source of truth and the row
    # is reactivated, not left dead. Keyed by external_id first, then
    # normalized_name for the rare row with no real code (e.g. a
    # group-header row whose "Код" cell holds a category title rather than
    # a real code is still usually stable month to month, but falls back to
    # name matching if that text ever changes).
    existing_by_external_id: dict[str, MasterProduct] = {}
    existing_by_normalized_name: dict[str, MasterProduct] = {}
    for row in db.query(MasterProduct).filter(MasterProduct.upload_id == target_upload_id).all():
        if row.external_id:
            existing_by_external_id.setdefault(row.external_id, row)
        elif row.normalized_name:
            existing_by_normalized_name.setdefault(row.normalized_name, row)

    matched_existing_ids: set[str] = set()
    stats = CatalogMergeStats(staging_upload_id=staging_upload.id)
    now = datetime.now(timezone.utc)

    staging_rows = db.query(MasterProduct).filter(MasterProduct.upload_id == staging_upload.id).all()

    for new_row in staging_rows:
        try:
            existing = None
            if new_row.external_id and new_row.external_id in existing_by_external_id:
                existing = existing_by_external_id[new_row.external_id]
            elif new_row.normalized_name and new_row.normalized_name in existing_by_normalized_name:
                existing = existing_by_normalized_name[new_row.normalized_name]

            if existing is not None:
                matched_existing_ids.add(existing.id)
                if not existing.is_active:
                    existing.is_active = True
                    stats.reactivated += 1
                for f in _MERGE_FIELDS:
                    setattr(existing, f, getattr(new_row, f))
                existing.updated_at = now
                stats.updated += 1
                db.delete(new_row)  # its data now lives on `existing`
            else:
                # A genuinely new product code. Every retrieval path
                # (loader.load_master_records, matching.find_exact_match,
                # this router's own list_products) scopes by a single
                # upload_id, not by CatalogVersion - a row left under the
                # throwaway staging upload would be invisible to
                # search/matching/the Catalog tab forever, so it is
                # re-parented onto the target catalog's real upload_id
                # rather than left where ingest_master happened to put it.
                new_row.upload_id = target_upload_id
                stats.inserted += 1
        except Exception as exc:  # noqa: BLE001 - one bad row must never abort the whole merge
            stats.errors.append({"external_id": new_row.external_id, "reason": str(exc)})

    # Exact, not approximate: a row is keyed into exactly one of the two
    # dicts above (external_id when present, else normalized_name), so
    # summing their remaining sizes double-counts nothing.
    stats.unmatched_existing = (
        len(existing_by_external_id) + len(existing_by_normalized_name) - len(matched_existing_ids)
    )

    catalog_version.product_count = (
        db.query(MasterProduct)
        .filter(
            MasterProduct.upload_id == target_upload_id,
            MasterProduct.is_active.is_(True),
            MasterProduct.is_group_header.is_(False),
        )
        .count()
    )

    db.commit()
    # Same "invalidate, don't rebuild synchronously" choice HANDOFF.md
    # section 13 already made for single-row edits - a merge can touch
    # thousands of rows, so rebuilding the index inline here would make a
    # routine monthly refresh take as long as the ~11s full rebuild
    # (section 6), for no benefit over letting the next real matching run
    # rebuild it lazily on its own cache miss.
    invalidate_cached_index_for_version(catalog_version.id)
    return stats
