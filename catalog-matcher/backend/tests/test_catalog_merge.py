"""Tests for app/services/catalog_merge.py - the "April -> May" incremental
catalog upsert (NEXT_STEPS.md). Follows test_ingestion.py's pattern of
building real, tiny .xlsx fixtures with openpyxl rather than mocking the
Excel layer, since column mapping / attribute extraction / error handling
all matter here exactly as much as they do for a normal ingest.
"""

import openpyxl
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import CatalogVersion, DestinationProduct, MasterProduct, Match, Upload
from app.services.catalog_merge import merge_master_file_into_version
from app.services.ingestion import ingest_master


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _write_xlsx(path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Catalog"
    ws.append(["Код", "Наименование", "Единица измерения", "Сметная цена, тенге"])
    for row in rows:
        ws.append(row)
    wb.save(path)
    return str(path)


@pytest.fixture
def april_catalog(db_session, tmp_path):
    """The "already ingested" state: a real Upload + CatalogVersion wrapping
    three products, the same way _create_catalog_version does it for real.
    """
    path = _write_xlsx(
        tmp_path / "april.xlsx",
        [
            ["521-101-0100", "Манеж детский", "шт.", 8000],
            ["521-101-0200", "Стул детский", "шт.", 3000],
            ["521-101-0300", "Стол детский", "шт.", 5000],
        ],
    )
    upload = ingest_master(db_session, path, "april.xlsx")
    db_session.commit()

    version = CatalogVersion(
        name="April", source_upload_id=upload.id, is_active=True, product_count=3
    )
    db_session.add(version)
    db_session.commit()
    return {"upload": upload, "version": version}


def test_matched_row_is_updated_in_place_same_id(db_session, april_catalog, tmp_path):
    """The whole point: a price/name change for an existing code must land
    on the SAME MasterProduct row, not a new one - otherwise every Match
    pointing at the old row's id would be silently stranded.
    """
    original = (
        db_session.query(MasterProduct).filter_by(external_id="521-101-0100").one()
    )
    original_id = original.id

    may_path = _write_xlsx(
        tmp_path / "may.xlsx",
        [
            ["521-101-0100", "Манеж детский (новая цена)", "шт.", 8500],  # price + name changed
            ["521-101-0200", "Стул детский", "шт.", 3000],  # unchanged
            ["521-101-0300", "Стол детский", "шт.", 5000],  # unchanged
        ],
    )

    stats = merge_master_file_into_version(
        db_session, april_catalog["version"], may_path, "may.xlsx"
    )

    assert stats.updated == 3  # every row is re-matched and copied over, even if unchanged
    assert stats.inserted == 0
    assert stats.reactivated == 0
    assert stats.unmatched_existing == 0

    refreshed = db_session.get(MasterProduct, original_id)
    assert refreshed is not None  # same row, not deleted
    assert refreshed.product_name == "Манеж детский (новая цена)"
    assert refreshed.price == 8500.0
    assert refreshed.updated_at is not None

    # No leftover duplicate row for the same code.
    assert (
        db_session.query(MasterProduct).filter_by(external_id="521-101-0100").count() == 1
    )


def test_new_code_is_inserted_and_reachable_under_the_target_upload(
    db_session, april_catalog, tmp_path
):
    may_path = _write_xlsx(
        tmp_path / "may.xlsx",
        [
            ["521-101-0100", "Манеж детский", "шт.", 8000],
            ["521-101-0200", "Стул детский", "шт.", 3000],
            ["521-101-0300", "Стол детский", "шт.", 5000],
            ["521-101-0400", "Кроватка детская", "шт.", 15000],  # brand new code
        ],
    )

    stats = merge_master_file_into_version(
        db_session, april_catalog["version"], may_path, "may.xlsx"
    )

    assert stats.inserted == 1
    assert stats.updated == 3

    new_row = db_session.query(MasterProduct).filter_by(external_id="521-101-0400").one()
    # Must be reachable the same way every other row in this catalog is -
    # loader.load_master_records and this router's own list_products both
    # scope by upload_id, not by CatalogVersion.
    assert new_row.upload_id == april_catalog["upload"].id


def test_row_absent_from_new_file_is_left_untouched_not_deleted(
    db_session, april_catalog, tmp_path
):
    """The literal ask this module was built for: a monthly refresh must
    never "completely remove" what was there before.
    """
    may_path = _write_xlsx(
        tmp_path / "may.xlsx",
        [
            ["521-101-0100", "Манеж детский", "шт.", 8000],
            ["521-101-0200", "Стул детский", "шт.", 3000],
            # 521-101-0300 ("Стол детский") is simply missing from May.
        ],
    )

    stats = merge_master_file_into_version(
        db_session, april_catalog["version"], may_path, "may.xlsx"
    )

    assert stats.unmatched_existing == 1
    still_there = db_session.query(MasterProduct).filter_by(external_id="521-101-0300").one()
    assert still_there.is_active is True
    assert still_there.product_name == "Стол детский"


def test_reappearing_soft_deleted_row_is_reactivated(db_session, april_catalog, tmp_path):
    deleted = db_session.query(MasterProduct).filter_by(external_id="521-101-0200").one()
    deleted.is_active = False
    db_session.commit()

    may_path = _write_xlsx(
        tmp_path / "may.xlsx",
        [
            ["521-101-0100", "Манеж детский", "шт.", 8000],
            ["521-101-0200", "Стул детский", "шт.", 3000],  # reappears in May
            ["521-101-0300", "Стол детский", "шт.", 5000],
        ],
    )

    stats = merge_master_file_into_version(
        db_session, april_catalog["version"], may_path, "may.xlsx"
    )

    assert stats.reactivated == 1
    refreshed = db_session.get(MasterProduct, deleted.id)
    assert refreshed.is_active is True


def test_match_history_survives_a_merge(db_session, april_catalog, tmp_path):
    """The whole reason this module updates rows in place instead of
    creating new ones: a Match confirmed against April's row must still
    resolve to that same real row after May is merged in - not get orphaned
    the way a "new CatalogVersion, new MasterProduct rows" approach would.
    """
    product = db_session.query(MasterProduct).filter_by(external_id="521-101-0100").one()
    original_id = product.id

    dest_upload = Upload(filename="дет_сад.xlsx", upload_type="destination", status="done")
    db_session.add(dest_upload)
    db_session.flush()
    destination = DestinationProduct(
        upload_id=dest_upload.id, source_row=2, product_name="Манеж детский",
        normalized_name="манеж детский", raw_data={},
    )
    db_session.add(destination)
    db_session.flush()

    match = Match(
        destination_product_id=destination.id,
        master_product_id=original_id,
        confidence=0.99,
        method="exact",
        is_confirmed=True,
        catalog_version_id=april_catalog["version"].id,
    )
    db_session.add(match)
    db_session.commit()

    may_path = _write_xlsx(
        tmp_path / "may.xlsx",
        [
            ["521-101-0100", "Манеж детский (обновлено)", "шт.", 9000],
            ["521-101-0200", "Стул детский", "шт.", 3000],
            ["521-101-0300", "Стол детский", "шт.", 5000],
        ],
    )
    merge_master_file_into_version(db_session, april_catalog["version"], may_path, "may.xlsx")

    db_session.refresh(match)
    resolved = db_session.get(MasterProduct, match.master_product_id)
    assert resolved is not None
    assert resolved.id == original_id
    assert resolved.product_name == "Манеж детский (обновлено)"  # updated, but still the same row


def test_product_count_recomputed_after_merge(db_session, april_catalog, tmp_path):
    may_path = _write_xlsx(
        tmp_path / "may.xlsx",
        [
            ["521-101-0100", "Манеж детский", "шт.", 8000],
            ["521-101-0400", "Кроватка детская", "шт.", 15000],
        ],
    )
    merge_master_file_into_version(db_session, april_catalog["version"], may_path, "may.xlsx")

    db_session.refresh(april_catalog["version"])
    # All 3 original rows stay active (2 of them simply absent from this
    # file, left untouched - not deleted) + 1 new code = 4 active rows.
    assert april_catalog["version"].product_count == 4


def test_merge_invalidates_the_cached_index(db_session, april_catalog, tmp_path, monkeypatch):
    calls = []
    import app.services.catalog_merge as merge_module

    monkeypatch.setattr(merge_module, "invalidate_cached_index_for_version", lambda vid: calls.append(vid))

    may_path = _write_xlsx(
        tmp_path / "may.xlsx",
        [["521-101-0100", "Манеж детский", "шт.", 8000]],
    )
    merge_master_file_into_version(db_session, april_catalog["version"], may_path, "may.xlsx")

    assert calls == [april_catalog["version"].id]


def test_staging_upload_leaves_no_orphaned_master_products(db_session, april_catalog, tmp_path):
    """Every staging row either gets deleted (matched case) or re-parented
    onto the target upload_id (new-code case) - nothing should be left
    dangling under the throwaway staging Upload.
    """
    may_path = _write_xlsx(
        tmp_path / "may.xlsx",
        [
            ["521-101-0100", "Манеж детский", "шт.", 8000],
            ["521-101-0500", "Новый товар", "шт.", 1000],
        ],
    )
    stats = merge_master_file_into_version(
        db_session, april_catalog["version"], may_path, "may.xlsx"
    )

    leftover = (
        db_session.query(MasterProduct)
        .filter(MasterProduct.upload_id == stats.staging_upload_id)
        .count()
    )
    assert leftover == 0
