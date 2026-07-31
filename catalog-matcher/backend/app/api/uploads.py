import json
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Project, Upload
from app.schemas import SheetInfo, UploadResult, UploadStatus
from app.services import excel_reader
from app.services.ingestion import IngestionOptions, ingest_destination, ingest_master

router = APIRouter(prefix="/api", tags=["uploads"])

ALLOWED_EXTENSIONS = {".xlsx", ".xlsm"}


def _save_temp_file(upload_file: UploadFile) -> Path:
    suffix = Path(upload_file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
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


def _resolve_project(db: Session, project_id: str | None) -> Project | None:
    """Validate `project_id` BEFORE spending time ingesting a large file.

    Ingesting Казниса апрель.xlsx takes a while; discovering afterwards
    that the project id was a typo would mean throwing that work away, or
    worse, silently keeping an upload that belongs to nothing.
    """
    if project_id is None:
        return None
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    return project


def _sheets_payload(path: Path) -> list[SheetInfo]:
    return [
        SheetInfo(
            name=s.name,
            row_count=s.row_count,
            detected_header_row=s.header_row_index,
            columns=s.headers,
        )
        for s in excel_reader.list_sheets(str(path))
    ]


@router.post("/uploads/master", response_model=UploadResult)
def upload_master(
    file: UploadFile,
    sheet_name: str | None = None,
    column_mapping: str | None = None,  # JSON string, e.g. {"product_name": "Наименование"}
    project_id: str | None = None,
    db: Session = Depends(get_db),
):
    """Upload a master catalog.

    Passing `project_id` both attaches the upload to that project AND pins
    it as that project's catalog, because uploading a catalog into a
    project is unambiguous about intent - there is exactly one catalog per
    project. Destinations are the many side, so they only attach.
    """
    project = _resolve_project(db, project_id)
    tmp_path = _save_temp_file(file)
    try:
        sheets = _sheets_payload(tmp_path)
        options = IngestionOptions(
            sheet_name=sheet_name,
            manual_column_mapping=json.loads(column_mapping) if column_mapping else None,
        )
        upload = ingest_master(db, str(tmp_path), file.filename, options)
        if project is not None:
            upload.project_id = project.id
            project.master_upload_id = upload.id
        db.commit()
        db.refresh(upload)
        return UploadResult(upload=UploadStatus.model_validate(upload), sheets=sheets)
    finally:
        tmp_path.unlink(missing_ok=True)


@router.post("/uploads/destination", response_model=UploadResult)
def upload_destination(
    file: UploadFile,
    sheet_name: str | None = None,
    column_mapping: str | None = None,
    project_id: str | None = None,
    db: Session = Depends(get_db),
):
    """Upload a destination (request) file.

    `project_id` attaches it to a project. A project can hold any number of
    these - that is the point of the project layer.
    """
    project = _resolve_project(db, project_id)
    tmp_path = _save_temp_file(file)
    try:
        sheets = _sheets_payload(tmp_path)
        options = IngestionOptions(
            sheet_name=sheet_name,
            manual_column_mapping=json.loads(column_mapping) if column_mapping else None,
        )
        upload = ingest_destination(db, str(tmp_path), file.filename, options)
        if project is not None:
            upload.project_id = project.id
        db.commit()
        db.refresh(upload)
        return UploadResult(upload=UploadStatus.model_validate(upload), sheets=sheets)
    finally:
        tmp_path.unlink(missing_ok=True)


@router.get("/uploads", response_model=list[UploadStatus])
def list_uploads(
    upload_type: str | None = None,
    project_id: str | None = None,
    db: Session = Depends(get_db),
):
    """List previously uploaded files, most recent first - so a user
    reopening the app can find and reuse an earlier destination upload's
    id instead of re-uploading the same file every session.

    `upload_type`: optional filter, "master" or "destination".
    `project_id`: optional filter. Pass "none" to list only uploads that
    belong to no project (useful for finding files to attach).
    """
    query = db.query(Upload)
    if upload_type is not None:
        query = query.filter(Upload.upload_type == upload_type)
    if project_id == "none":
        query = query.filter(Upload.project_id.is_(None))
    elif project_id is not None:
        query = query.filter(Upload.project_id == project_id)
    uploads = query.order_by(Upload.created_at.desc()).all()
    return [UploadStatus.model_validate(u) for u in uploads]


@router.get("/uploads/{upload_id}/status", response_model=UploadStatus)
def get_upload_status(upload_id: str, db: Session = Depends(get_db)):
    upload = db.get(Upload, upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")
    return UploadStatus.model_validate(upload)
