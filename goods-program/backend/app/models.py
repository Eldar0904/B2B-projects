import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    """A matching project: one pinned master catalog + many destination files.

    Added to support the real workflow, where a single approved catalog
    ("Казниса апрель") is matched against many different request lists
    (детсад, школа, больница...), each with its own columns, its own review
    progress and its own export.

    Before this existed, every destination upload was matched against
    "every MasterProduct row ever ingested". That is wrong in two
    directions: a stale catalog from an earlier import silently pollutes a
    new run's candidates, and there was no way to express "these five
    request files all belong to the April catalog".
    `standalone_matching.py` had already hit this and worked around it by
    threading a `master_upload_id` parameter through by hand - this makes
    the relationship a first-class thing instead of a parameter every
    caller must remember to pass.

    `master_upload_id` is nullable so a project can be created before its
    catalog has been uploaded.
    """

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The catalog every destination in this project is matched against.
    master_upload_id: Mapped[str | None] = mapped_column(
        ForeignKey("uploads.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    uploads: Mapped[list["Upload"]] = relationship(
        back_populates="project", foreign_keys="Upload.project_id"
    )


class Upload(Base):
    """One uploaded Excel file (master or destination)."""

    __tablename__ = "uploads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)

    # Nullable on purpose: uploads created before projects existed have no
    # project, and the standalone wizard still creates project-less
    # one-shot uploads. Nullable also means this migrates without having to
    # rewrite or backfill any existing row.
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )

    filename: Mapped[str] = mapped_column(String(512))
    upload_type: Mapped[str] = mapped_column(String(20))  # "master" | "destination"
    sheet_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    processed_rows: Mapped[int] = mapped_column(Integer, default=0)
    skipped_rows: Mapped[int] = mapped_column(Integer, default=0)
    error_report: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    # foreign_keys is required here because there are TWO paths between
    # Project and Upload (Upload.project_id and Project.master_upload_id);
    # without it SQLAlchemy cannot tell which one this relationship means
    # and raises AmbiguousForeignKeysError at mapper configuration time.
    project: Mapped["Project | None"] = relationship(
        back_populates="uploads", foreign_keys=[project_id]
    )

    master_products: Mapped[list["MasterProduct"]] = relationship(back_populates="upload")
    destination_products: Mapped[list["DestinationProduct"]] = relationship(back_populates="upload")


class MasterProduct(Base):
    """A row from the master catalog Excel (Source A)."""

    __tablename__ = "master_products"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    upload_id: Mapped[str] = mapped_column(ForeignKey("uploads.id"))
    source_row: Mapped[int] = mapped_column(Integer)

    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    product_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    price: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    freight_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gross_weight_kg: Mapped[float | None] = mapped_column(Numeric(12, 3), nullable=True)
    is_group_header: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- HANDOFF.md section 5 (Task 2): parsed attributes, additive only ---
    # Populated by app/services/attributes.py during ingestion from
    # product_name/description (and a dedicated "Размеры"/"Единица
    # измерения" column when the sheet has one) - never by rewriting
    # product_name or raw_data, which stay exactly as ingested. All
    # nullable: an unparsed attribute is an honest "unknown", not a 0 or a
    # guess - see attributes.py's own docstring for why.
    dim_w_mm: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    dim_h_mm: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    dim_d_mm: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    material: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unit_normalized: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # --- Catalog management tab: soft delete (HANDOFF.md section 13) -------
    # `Match.master_product_id`, `MatchCandidate.master_product_id`, and
    # `Feedback.selected_master_product_id` all reference this row's `id`
    # with no ON DELETE rule, so a hard DELETE of a row still referenced by
    # a confirmed match would orphan those rows or raise an integrity
    # error depending on the database. Soft-deleting instead (set this to
    # False) keeps every match/audit trail pointing at a real row while
    # hiding it from search, matching, and the catalog tab's default view -
    # see app/api/catalog.py for where this is read and written, and
    # scripts/migrate_add_master_product_active_flag.py for the migration.
    # Reversible on purpose: this project treats deletion as something that
    # should be a separate, explicit, undoable action (see
    # app/api/projects.py's delete_project for the same principle applied
    # to projects).
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # --- Incremental catalog refresh (April -> May-style updates) ----------
    # NULL means "never touched by a merge" - a row that has only ever come
    # from its original ingestion, which is the honest default for every
    # pre-existing row (same "NULL is an honest unknown, not a guess"
    # convention as the attribute columns above). Set by
    # app/services/catalog_merge.py whenever a later file's row is folded
    # into this one in place - see that module's docstring for why an
    # update-in-place (same id, not a new row) matters for keeping
    # Match/Feedback history meaningful across a catalog refresh.
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    raw_data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    upload: Mapped["Upload"] = relationship(back_populates="master_products")


class DestinationProduct(Base):
    """A row from a destination Excel (Source B), e.g. the детсад sheet."""

    __tablename__ = "destination_products"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    upload_id: Mapped[str] = mapped_column(ForeignKey("uploads.id"))
    source_row: Mapped[int] = mapped_column(Integer)

    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    product_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity: Mapped[float | None] = mapped_column(Numeric(14, 3), nullable=True)
    price: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")

    # Phase 8: gap between the top-2 reranked candidates' scores, computed
    # by POST /api/matching/{upload_id}/prioritize. NULL until that batch
    # action runs; smaller = more uncertain = higher review priority.
    uncertainty_margin: Mapped[float | None] = mapped_column(Numeric(6, 5), nullable=True)

    # --- HANDOFF.md section 5 (Task 2): parsed attributes, additive only ---
    # Same fields and same reasoning as MasterProduct's, so both sides can
    # be compared attribute-for-attribute later. `quantity_normalized`
    # exists only here, not on MasterProduct: a catalog row has no
    # requested amount to normalize (its `quantity`-shaped concept is
    # `price`/unit pricing), so a mirrored column there would always be
    # NULL with nothing to explain it.
    dim_w_mm: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    dim_h_mm: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    dim_d_mm: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    material: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unit_normalized: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quantity_normalized: Mapped[float | None] = mapped_column(Numeric(14, 3), nullable=True)

    raw_data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    upload: Mapped["Upload"] = relationship(back_populates="destination_products")


# --- Forward-compatible tables (spec section 31). Not populated until later phases. ---


class CatalogVersion(Base):
    """A named, immutable snapshot of the master catalog at one point in
    time (HANDOFF.md section 4).

    Why this exists: before it did, the standalone wizard re-ingested the
    catalog Excel and rebuilt the whole search index (BM25 + fuzzy +
    vectors) on every single run - ~11s over 5,163 rows - and since
    MasterProduct rows are never deleted, every run left another full copy
    of the catalog sitting in the table. Wrapping one ingested Upload in a
    CatalogVersion is what lets a later run say "reuse the April catalog"
    instead of "re-read and re-index the April catalog".

    `source_upload_id` points at the master Upload that was actually
    ingested to produce this version - that Upload's MasterProduct rows
    are this version's product set (see loader.load_master_records's
    upload_id scoping, which this reuses unchanged).

    `is_active` marks the version new runs should default to when the user
    picks "use existing catalog" without specifying one - the most
    recently ingested version, normally. It is deliberately NOT unique: the
    reindex/adopt path may need more than one version active during a
    transition, and enforcing single-active-only at the DB layer buys
    nothing that the "most recent" query below doesn't already give.

    `product_count` is a cached count of non-group-header MasterProduct
    rows at creation time, so the catalog picker can show "5,163 products"
    without a COUNT(*) query per row in the dropdown.
    """

    __tablename__ = "catalog_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255))
    source_upload_id: Mapped[str] = mapped_column(ForeignKey("uploads.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    product_count: Mapped[int] = mapped_column(Integer, default=0)

    source_upload: Mapped["Upload"] = relationship()


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    destination_product_id: Mapped[str] = mapped_column(ForeignKey("destination_products.id"))
    master_product_id: Mapped[str] = mapped_column(ForeignKey("master_products.id"))
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    match_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    # Nullable, and deliberately not backfilled to a guessed value for rows
    # that predate this column (see migrate_add_catalog_versions.py) -
    # HANDOFF.md section 4 is explicit that a wrong guess here is worse
    # than an honest NULL, since this is the field that lets a reviewer's
    # confirmed match survive the catalog being replaced later without
    # silently starting to mean a different product.
    catalog_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("catalog_versions.id"), nullable=True
    )


class MatchCandidate(Base):
    __tablename__ = "match_candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    destination_product_id: Mapped[str] = mapped_column(ForeignKey("destination_products.id"))
    master_product_id: Mapped[str] = mapped_column(ForeignKey("master_products.id"))
    rank: Mapped[int] = mapped_column(Integer)
    vector_score: Mapped[float | None] = mapped_column(Numeric(6, 5), nullable=True)
    keyword_score: Mapped[float | None] = mapped_column(Numeric(6, 5), nullable=True)
    fuzzy_score: Mapped[float | None] = mapped_column(Numeric(6, 5), nullable=True)
    reranker_score: Mapped[float | None] = mapped_column(Numeric(6, 5), nullable=True)
    final_score: Mapped[float | None] = mapped_column(Numeric(6, 5), nullable=True)


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    destination_product_id: Mapped[str] = mapped_column(ForeignKey("destination_products.id"))
    selected_master_product_id: Mapped[str | None] = mapped_column(
        ForeignKey("master_products.id"), nullable=True
    )
    decision_type: Mapped[str] = mapped_column(String(32))
    candidate_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
