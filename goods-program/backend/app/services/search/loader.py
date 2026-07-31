"""Loads MasterProduct rows out of the DB into the plain-dataclass form
the search package works with (see types.py for why)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import MasterProduct
from app.services.search.types import MasterProductRecord


def load_master_records(
    db: Session, upload_id: str | None = None, include_inactive: bool = False
) -> list[MasterProductRecord]:
    """Load MasterProduct rows for indexing.

    By default (`upload_id=None`) loads every MasterProduct row ever
    ingested - the right behavior for the main app, where the master
    catalog is meant to be one continuously-updated global catalog and
    `/api/search/reindex` / the startup auto-rebuild both intentionally
    see everything.

    Pass `upload_id` to scope to a single upload instead. This exists for
    the standalone matching wizard (see `standalone_matching.run_matching_job`),
    which creates a brand-new master upload on every run and must build its
    search index from *only* that upload - otherwise, since master rows are
    never deleted, every earlier wizard run (including any mistaken catalog
    file dropped in during testing) stays in the table forever and silently
    pollutes every later run's matches with candidates from the wrong file.

    Excludes soft-deleted rows (`is_active = False`, HANDOFF.md section 13 -
    the catalog management tab) by default, the same way this function
    already excludes nothing else silently - a row a reviewer explicitly
    removed from the catalog must not keep surfacing as a match candidate.
    Pass `include_inactive=True` only for tooling that needs to see
    everything regardless (there is no such caller today).
    """
    query = db.query(MasterProduct)
    if upload_id is not None:
        query = query.filter(MasterProduct.upload_id == upload_id)
    if not include_inactive:
        query = query.filter(MasterProduct.is_active.is_(True))
    rows = query.all()
    return [
        MasterProductRecord(
            id=row.id,
            external_id=row.external_id,
            normalized_name=row.normalized_name or "",
            description=row.description,
            is_group_header=row.is_group_header,
        )
        for row in rows
    ]
