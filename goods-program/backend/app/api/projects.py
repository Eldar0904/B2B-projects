"""Project API: group one master catalog with many destination files.

This is the router that makes multi-destination work usable. The shape is
deliberately small - a project is a folder with a pinned catalog, not a
workflow engine.

    POST   /api/projects                          create
    GET    /api/projects                          list, with counts
    GET    /api/projects/{id}                     one project + its uploads
    PUT    /api/projects/{id}/master/{upload_id}  pin the catalog
    POST   /api/projects/{id}/destinations        attach a destination upload
    POST   /api/projects/{id}/reindex             build the search index
    DELETE /api/projects/{id}                     delete (uploads survive)

--- A note on the FastAPI patterns used here, since they recur ----------

`APIRouter(prefix=..., tags=...)` is a group of endpoints that `main.py`
mounts with `app.include_router(...)`. The prefix is applied to every path
below, so the decorators only spell out the part after `/api/projects`.

`db: Session = Depends(get_db)` is dependency injection. FastAPI calls
`get_db()` (in `database.py`), which yields a session and closes it in a
`finally` block once the response is sent. That is why no endpoint here
opens or closes a session by hand, and why forgetting to close one is not
a class of bug that can occur.

`response_model=ProjectRead` makes FastAPI validate and serialize what is
returned. Returning a SQLAlchemy object works because the schema sets
`from_attributes = True`; FastAPI reads the declared fields off the ORM
object and silently drops anything not declared. That is a feature - it
means an internal column can never leak into the API by accident.

Raising `HTTPException(404, ...)` is the normal way to produce an error
response; there is no need to construct a Response object.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import DestinationProduct, MasterProduct, Project, Upload
from app.schemas import (
    ProjectCreate,
    ProjectDetail,
    ProjectRead,
    ProjectUploadSummary,
)
from app.services.search.index_manager import get_index
from app.services.search.loader import load_master_records

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _get_project_or_404(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    return project


def _upload_summary(db: Session, upload: Upload) -> ProjectUploadSummary:
    """Row counts per upload, so the UI can show progress without a second
    round-trip per file.
    """
    if upload.upload_type == "master":
        product_count = (
            db.query(func.count(MasterProduct.id))
            .filter(MasterProduct.upload_id == upload.id)
            .scalar()
        )
        matched = None
        pending = None
    else:
        product_count = (
            db.query(func.count(DestinationProduct.id))
            .filter(DestinationProduct.upload_id == upload.id)
            .scalar()
        )
        matched = (
            db.query(func.count(DestinationProduct.id))
            .filter(
                DestinationProduct.upload_id == upload.id,
                DestinationProduct.status == "matched",
            )
            .scalar()
        )
        pending = (
            db.query(func.count(DestinationProduct.id))
            .filter(
                DestinationProduct.upload_id == upload.id,
                DestinationProduct.status == "pending",
            )
            .scalar()
        )

    return ProjectUploadSummary(
        id=upload.id,
        filename=upload.filename,
        upload_type=upload.upload_type,
        sheet_name=upload.sheet_name,
        status=upload.status,
        product_count=product_count or 0,
        matched_count=matched,
        pending_count=pending,
        created_at=upload.created_at,
    )


@router.post("", response_model=ProjectRead, status_code=201)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> Project:
    """Create an empty project. The catalog is attached separately, so a
    project can exist before anyone has uploaded a spreadsheet.
    """
    project = Project(name=payload.name, description=payload.description)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("", response_model=list[ProjectRead])
def list_projects(db: Session = Depends(get_db)) -> list[Project]:
    return db.query(Project).order_by(Project.created_at.desc()).all()


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project(project_id: str, db: Session = Depends(get_db)) -> ProjectDetail:
    project = _get_project_or_404(db, project_id)
    uploads = (
        db.query(Upload)
        .filter(Upload.project_id == project.id)
        .order_by(Upload.created_at.desc())
        .all()
    )
    return ProjectDetail(
        id=project.id,
        name=project.name,
        description=project.description,
        master_upload_id=project.master_upload_id,
        created_at=project.created_at,
        destinations=[
            _upload_summary(db, u) for u in uploads if u.upload_type == "destination"
        ],
        master=next(
            (_upload_summary(db, u) for u in uploads if u.id == project.master_upload_id),
            None,
        ),
    )


@router.put("/{project_id}/master/{upload_id}", response_model=ProjectRead)
def set_master_catalog(
    project_id: str, upload_id: str, db: Session = Depends(get_db)
) -> Project:
    """Pin an already-uploaded master catalog to this project.

    Rejects a destination upload with 400 rather than accepting it and
    producing an empty catalog later - the failure would otherwise surface
    much further away, as "no candidates found for every product".
    """
    project = _get_project_or_404(db, project_id)
    upload = db.get(Upload, upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail=f"Upload {upload_id} not found")
    if upload.upload_type != "master":
        raise HTTPException(
            status_code=400,
            detail=f"Upload {upload_id} is a '{upload.upload_type}' upload, "
            "but a project's master catalog must be a 'master' upload.",
        )

    project.master_upload_id = upload.id
    upload.project_id = project.id
    db.commit()
    db.refresh(project)
    return project


@router.post("/{project_id}/destinations/{upload_id}", response_model=ProjectDetail)
def attach_destination(
    project_id: str, upload_id: str, db: Session = Depends(get_db)
) -> ProjectDetail:
    """Attach an existing destination upload to this project."""
    project = _get_project_or_404(db, project_id)
    upload = db.get(Upload, upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail=f"Upload {upload_id} not found")
    if upload.upload_type != "destination":
        raise HTTPException(
            status_code=400,
            detail=f"Upload {upload_id} is a '{upload.upload_type}' upload; "
            "only 'destination' uploads can be attached this way. "
            "Use PUT /api/projects/{project_id}/master/{upload_id} for a catalog.",
        )

    upload.project_id = project.id
    db.commit()
    return get_project(project_id, db)


@router.post("/{project_id}/reindex")
def reindex_project(project_id: str, db: Session = Depends(get_db)) -> dict:
    """Rebuild the in-memory search index from THIS project's catalog only.

    Scoping matters. The global `/api/search/reindex` loads every
    MasterProduct row ever ingested, so a stale catalog from a previous
    import silently contributes candidates. Building from
    `project.master_upload_id` is what makes a project's matches
    reproducible.
    """
    project = _get_project_or_404(db, project_id)
    if project.master_upload_id is None:
        raise HTTPException(
            status_code=400,
            detail="This project has no master catalog yet. Upload one, then "
            "PUT /api/projects/{project_id}/master/{upload_id}.",
        )

    records = load_master_records(db, upload_id=project.master_upload_id)
    if not records:
        raise HTTPException(
            status_code=400,
            detail=f"Master upload {project.master_upload_id} contains no products. "
            "Check the upload's error_report.",
        )

    stats = get_index().build(records)
    return {
        "project_id": project.id,
        "master_upload_id": project.master_upload_id,
        "total_records": stats.total_records,
        "indexed_records": stats.indexed_records,
        "group_headers_excluded": stats.group_headers_excluded,
        "embedding_dim": stats.embedding_dim,
    }


@router.delete("/{project_id}", status_code=204, response_model=None)
def delete_project(project_id: str, db: Session = Depends(get_db)) -> None:
    """Delete the project only.

    Uploads and their products are detached (project_id -> NULL), never
    deleted. Removing a grouping should not destroy ingested data or the
    review work already done against it; if the user really wants the rows
    gone, that is a separate, explicit action.
    """
    project = _get_project_or_404(db, project_id)
    db.query(Upload).filter(Upload.project_id == project.id).update(
        {Upload.project_id: None}, synchronize_session=False
    )
    db.delete(project)
    db.commit()
