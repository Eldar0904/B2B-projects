"""
SQLAlchemy models.

Design note: `CatalogSource` scopes products so government smeta and supplier
catalogs can coexist. Products belong to a `CatalogVersion` (periodic release).
Per-source field defs + import mappings support layout editing without
hardcoding Excel headers in the matching engine.
"""
from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Float, Text, ForeignKey, DateTime, JSON, Boolean, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


class CatalogSource(Base):
    """A catalog we can match against, e.g. 'government', 'supplier_x'."""
    __tablename__ = "catalog_sources"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    description = Column(String(500), nullable=True)
    kind = Column(String(64), default="government", nullable=False)  # government | supplier
    is_enabled = Column(Boolean, default=True, nullable=False)  # global skip when False
    is_archived = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    products = relationship("CatalogProduct", back_populates="source", cascade="all, delete-orphan")
    versions = relationship("CatalogVersion", back_populates="source", cascade="all, delete-orphan")
    field_defs = relationship("CatalogFieldDef", back_populates="source", cascade="all, delete-orphan")
    import_mappings = relationship(
        "CatalogImportMapping", back_populates="source", cascade="all, delete-orphan"
    )


class CatalogVersion(Base):
    """Periodic catalog release (e.g. quarterly smeta update)."""
    __tablename__ = "catalog_versions"
    __table_args__ = (
        UniqueConstraint("source_id", "label", name="uq_catalog_version_source_label"),
    )

    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey("catalog_sources.id"), nullable=False, index=True)
    label = Column(String(128), nullable=False)  # e.g. 2026-Q2
    effective_from = Column(DateTime, nullable=True)
    is_current = Column(Boolean, default=False, nullable=False)
    notes = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    source = relationship("CatalogSource", back_populates="versions")
    products = relationship("CatalogProduct", back_populates="version")


class CatalogProduct(Base):
    """A single row from a catalog (e.g. one government-catalog product)."""
    __tablename__ = "catalog_products"

    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey("catalog_sources.id"), nullable=False, index=True)
    version_id = Column(Integer, ForeignKey("catalog_versions.id"), nullable=True, index=True)

    code = Column(String(255), index=True)          # Government Code
    name = Column(String(1000))                      # Product Name
    brand = Column(String(500))
    model = Column(String(500))
    description = Column(Text)
    technical_specs = Column(Text)
    price = Column(Float, nullable=True)

    # Category derived from government code prefix (e.g. 521-201) or Excel column
    category_code = Column(String(64), index=True, nullable=True)
    category_name = Column(String(500), nullable=True)

    # Normalized text used for matching (lowercased, cleaned, concatenated)
    normalized_text = Column(Text)

    # Extra per-source fields (keys match CatalogFieldDef.key where is_core=False)
    custom_fields = Column(JSON, nullable=True)

    is_active = Column(Boolean, default=True, nullable=False)

    # Semantic embedding (JSON list of floats) + model name used to produce it
    embedding_json = Column(Text, nullable=True)
    embedding_model = Column(String(255), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    source = relationship("CatalogSource", back_populates="products")
    version = relationship("CatalogVersion", back_populates="products")


class CatalogFieldDef(Base):
    """Per-source field layout (core + custom)."""
    __tablename__ = "catalog_field_defs"
    __table_args__ = (
        UniqueConstraint("source_id", "key", name="uq_catalog_field_source_key"),
    )

    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey("catalog_sources.id"), nullable=False, index=True)
    key = Column(String(128), nullable=False)
    label = Column(String(255), nullable=False)
    field_type = Column(String(32), default="string", nullable=False)  # string|number|text
    is_core = Column(Boolean, default=False, nullable=False)
    is_required = Column(Boolean, default=False, nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)
    show_in_table = Column(Boolean, default=True, nullable=False)
    use_in_matching = Column(Boolean, default=False, nullable=False)

    source = relationship("CatalogSource", back_populates="field_defs")


class CatalogImportMapping(Base):
    """Maps an Excel header (as seen in file) to a field key for a source."""
    __tablename__ = "catalog_import_mappings"
    __table_args__ = (
        UniqueConstraint("source_id", "excel_header", name="uq_import_map_source_header"),
    )

    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey("catalog_sources.id"), nullable=False, index=True)
    excel_header = Column(String(255), nullable=False)
    field_key = Column(String(128), nullable=False)

    source = relationship("CatalogSource", back_populates="import_mappings")


class Project(Base):
    """Fitout project that can bind a range of supplier/government catalogs."""
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    code = Column(String(128), nullable=True, index=True)
    description = Column(String(1000), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    catalog_links = relationship(
        "ProjectCatalogLink", back_populates="project", cascade="all, delete-orphan"
    )
    items = relationship("InternalItem", back_populates="project")


class ProjectCatalogLink(Base):
    """Which catalogs a project uses, with per-project include/skip."""
    __tablename__ = "project_catalog_links"
    __table_args__ = (
        UniqueConstraint("project_id", "source_id", name="uq_project_catalog_link"),
    )

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    source_id = Column(Integer, ForeignKey("catalog_sources.id"), nullable=False, index=True)
    include_in_matching = Column(Boolean, default=True, nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)

    project = relationship("Project", back_populates="catalog_links")
    source = relationship("CatalogSource")


class InternalItem(Base):
    """A row from Our_Items.xlsx to be matched against a catalog."""
    __tablename__ = "internal_items"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)

    item_code = Column(String(255), index=True)
    item_name = Column(String(1000))
    description = Column(Text)
    quantity = Column(Float, nullable=True)

    category_code = Column(String(64), index=True, nullable=True)
    category_name = Column(String(500), nullable=True)

    normalized_text = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="items")
    matches = relationship("MatchResult", back_populates="item", cascade="all, delete-orphan")


class MatchResult(Base):
    """One candidate match (top-N) proposed for an internal item."""
    __tablename__ = "match_results"

    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("internal_items.id"), nullable=False)
    catalog_product_id = Column(Integer, ForeignKey("catalog_products.id"), nullable=False)

    rank = Column(Integer)                # 1, 2, 3 (top-N ordering)
    confidence_score = Column(Float)       # 0..1 similarity-derived score
    explanation = Column(Text)             # human-readable rationale

    is_selected = Column(Integer, default=0)  # 1 if user/system picked this as final match
    is_manual_override = Column(Integer, default=0)  # 1 if user manually changed the match

    created_at = Column(DateTime, default=datetime.utcnow)

    item = relationship("InternalItem", back_populates="matches")
    catalog_product = relationship("CatalogProduct")


class MatchingRun(Base):
    """Metadata about a matching batch run, for auditability."""
    __tablename__ = "matching_runs"

    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey("catalog_sources.id"), nullable=True)
    engine_name = Column(String(100))       # e.g. "tfidf_v1"
    params = Column(JSON, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    items_processed = Column(Integer, default=0)
