"""Backend adapter for the standalone matching wizard tab (see
app/services/standalone_matching.py for the full design rationale).

Endpoint contract matches `frontend/public/matching/app.js` exactly (that
file was written against this contract, not the other way around):

    GET  /api/v1/matching/catalogs
    GET  /api/v1/matching/destinations/resumable
    POST /api/v1/matching/excel/preview
    POST /api/v1/matching/excel/run/start
    GET  /api/v1/matching/jobs/{job_id}
    POST /api/v1/matching/decisions
    POST /api/v1/matching/manual-search
    POST /api/v1/matching/save

`GET /catalogs` and `catalog_version_id` on `run/start`/`save` are from
HANDOFF.md section 4, "use existing catalog": they let a run reuse an
already-ingested master catalog instead of re-uploading and re-indexing
the same Excel file every time. `catalog_file` remains supported and
required on first use of a catalog - see standalone_matching.run_matching_job.

`GET /destinations/resumable`, `destination_upload_id` on `run/start`, and
`POST /decisions` are the "no resume" fix (NEXT_STEPS.md item 6): every
wizard decision is persisted the instant it's made (`/decisions`, Part A),
and a previous destination upload can be reopened instead of re-uploaded
(`destination_upload_id` on `run/start`, Part B) - see
standalone_matching.run_matching_job and `_resume_record_for_decided`.

`POST /manual-search` is NEXT_STEPS.md item 6's other bullet: when none of
the 3 auto-suggested candidates fit, search the whole catalog instead of
only being able to reject - see standalone_matching.manual_search.
"""

from __future__ import annotations

import io
import tempfile
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import CatalogVersion, DestinationProduct
from app.services import excel_reader, standalone_matching

router = APIRouter(prefix="/api/v1/matching", tags=["standalone-matching"])

_ALLOWED_EXTENSIONS = {".xlsx", ".xlsm"}


def _save_temp_file(upload_file: UploadFile) -> Path:
    suffix = Path(upload_file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix or 'unknown'}")

    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    size = 0
    with tmp:
        while chunk := upload_file.file.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                raise HTTPException(status_code=413, detail="File exceeds max upload size")
            tmp.write(chunk)
    return Path(tmp.name)


def _row_count(path: Path) -> int:
    sheets = excel_reader.list_sheets(str(path))
    if not sheets:
        return 0
    sheet = max(sheets, key=lambda s: s.row_count)
    return max(sheet.row_count - sheet.header_row_index - 1, 0)


@router.get("/catalogs")
async def list_catalogs(db: Session = Depends(get_db)):
    """Feeds the wizard's "use existing catalog" picker. Newest first."""
    versions = standalone_matching.list_catalog_versions(db)
    return {
        "catalogs": [
            {
                "id": v.id,
                "name": v.name,
                "product_count": v.product_count,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in versions
        ]
    }


@router.post("/excel/preview")
async def preview(destination_file: UploadFile | None = None, catalog_file: UploadFile | None = None):
    """Row counts only, for the drop-zone hint text - no ingestion, no
    database writes.
    """
    destination_count = None
    catalog_count = None

    if destination_file is not None:
        path = _save_temp_file(destination_file)
        try:
            destination_count = _row_count(path)
        finally:
            path.unlink(missing_ok=True)

    if catalog_file is not None:
        path = _save_temp_file(catalog_file)
        try:
            catalog_count = _row_count(path)
        finally:
            path.unlink(missing_ok=True)

    return {"destination_count": destination_count, "catalog_count": catalog_count}


@router.post("/excel/run/start")
async def run_start(
    destination_file: UploadFile | None = None,
    catalog_file: UploadFile | None = None,
    catalog_version_id: str | None = None,
    destination_upload_id: str | None = None,
    db: Session = Depends(get_db),
):
    """Kicks off a background job that classifies every destination product.
    Returns immediately with a job_id + total for the frontend to poll.

    Exactly one catalog source is required: either `catalog_file` (ingested
    fresh, same as before - and wrapped into a new, reusable CatalogVersion)
    or `catalog_version_id` (an already-ingested catalog, reused without
    re-ingesting or re-indexing - see run_matching_job). If both are given,
    `catalog_version_id` wins - a picker offering "use existing" would
    normally leave the file input empty anyway, so this only matters for a
    caller deliberately sending both.

    Similarly, exactly one destination source is required: either
    `destination_file` (a fresh upload, the original behavior) or
    `destination_upload_id` ("continue a previous run" - NEXT_STEPS.md item
    6, Part B of the "no resume" fix). Resuming skips ingestion entirely and
    reuses the existing DestinationProduct rows - `build_job_result` already
    knows to report an already-decided row's real decision instead of
    reclassifying it (`_resume_record_for_decided`), which is what makes
    this an actual resume rather than a second, parallel review pass over
    the same rows.
    """
    if catalog_file is None and not catalog_version_id:
        raise HTTPException(
            status_code=400,
            detail="Provide either catalog_file (new upload) or catalog_version_id (reuse an existing catalog).",
        )
    if destination_file is None and not destination_upload_id:
        raise HTTPException(
            status_code=400,
            detail="Provide either destination_file (new upload) or destination_upload_id (continue a previous run).",
        )

    destination_path = _save_temp_file(destination_file) if destination_file is not None else None
    catalog_path = _save_temp_file(catalog_file) if (catalog_file is not None and not catalog_version_id) else None

    if destination_upload_id:
        # No fresh file to count rows from - the row count already exists
        # in the database from whenever this upload was originally ingested.
        total = (
            db.query(DestinationProduct)
            .filter(DestinationProduct.upload_id == destination_upload_id)
            .count()
        )
    else:
        total = _row_count(destination_path)
    job_id = standalone_matching.create_job(total)

    def _run():
        try:
            standalone_matching.run_matching_job(
                job_id,
                str(catalog_path) if catalog_path is not None else None,
                catalog_file.filename if catalog_file is not None else None,
                str(destination_path) if destination_path is not None else None,
                destination_file.filename if destination_file is not None else None,
                catalog_version_id=catalog_version_id,
                destination_upload_id=destination_upload_id,
            )
        finally:
            if catalog_path is not None:
                catalog_path.unlink(missing_ok=True)
            if destination_path is not None:
                destination_path.unlink(missing_ok=True)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return {"job_id": job_id, "total": total}


@router.get("/destinations/resumable")
async def destinations_resumable(db: Session = Depends(get_db)):
    """Feeds the wizard's "continue a previous run" picker (Part B of the
    "no resume" fix). See standalone_matching.list_resumable_destinations.
    """
    return {"destinations": standalone_matching.list_resumable_destinations(db)}


@router.post("/manual-search")
async def manual_search(request: Request, db: Session = Depends(get_db)):
    """Manual catalog search inside the wizard (NEXT_STEPS.md item 6) - for
    when none of the 3 auto-suggested candidates fit. See
    standalone_matching.manual_search for why this is scoped to a specific
    catalog_version_id rather than reusing app/api/matching.py's own
    manual-search endpoint.
    """
    body = await request.json()
    query = (body.get("query") or "").strip()
    catalog_version_id = body.get("catalog_version_id")
    top_k = body.get("top_k") or 10

    if not catalog_version_id:
        raise HTTPException(status_code=400, detail="catalog_version_id is required")
    version = db.get(CatalogVersion, catalog_version_id)
    if version is None:
        raise HTTPException(status_code=404, detail=f"Catalog version {catalog_version_id} not found")
    if not query:
        return {"candidates": []}

    return {"candidates": standalone_matching.manual_search(db, version, query, top_k=top_k)}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = standalone_matching.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "status": job.status,
        "percent": job.percent,
        "current": job.current,
        "total": job.total,
        "result": job.result,
        "error": job.error,
    }


@router.post("/decisions")
async def save_decisions(request: Request, db: Session = Depends(get_db)):
    """Part A of the "no resume" fix (NEXT_STEPS.md item 6): persist one
    (or a few) wizard decisions THE INSTANT they're made, instead of only
    at the final "Save & Export" click. Same body shape as `/save` minus
    the export - `app.js`'s `saveChoice()` fires this immediately after
    every candidate pick / "Не подходит" click, so a decision already made
    is never lost to a crashed tab or a closed browser.

    Deliberately does NOT return a file - this fires on every single click
    during review, not once at the end; a StreamingResponse per click would
    be wasted work the reviewer never sees. `/save` still returns the export
    - it just skips re-persisting anything already sent here, via
    `already_persisted` (see save_results's own docstring).
    """
    body = await request.json()
    rows = body.get("rows", [])
    catalog_version_id = body.get("catalog_version_id")

    standalone_matching.save_results(db, rows, catalog_version_id=catalog_version_id)
    db.commit()
    return {"saved": len(rows)}


@router.post("/save")
async def save(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    rows = body.get("rows", [])
    catalog_version_id = body.get("catalog_version_id")

    standalone_matching.save_results(db, rows, catalog_version_id=catalog_version_id)
    db.commit()

    workbook_bytes = standalone_matching.build_export_workbook(rows)
    return StreamingResponse(
        io.BytesIO(workbook_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="matching_result.xlsx"'},
    )
