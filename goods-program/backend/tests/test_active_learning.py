import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import DestinationProduct, MasterProduct, Upload
from app.services import matching
from app.services.search.index_manager import CatalogSearchIndex
from app.services.search.loader import load_master_records


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def _isolated_qdrant_storage(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "qdrant_local_path", str(tmp_path / "qdrant"))
    monkeypatch.setattr(settings, "qdrant_url", None)
    monkeypatch.setattr(settings, "embedding_provider", "tfidf")


def _seed(db_session):
    master_upload = Upload(filename="master.xlsx", upload_type="master", status="done")
    dest_upload = Upload(filename="dest.xlsx", upload_type="destination", status="done")
    db_session.add_all([master_upload, dest_upload])
    db_session.flush()

    master_products = [
        # Two very similar tables - ambiguous for a "table" query.
        MasterProduct(
            upload_id=master_upload.id, source_row=2, external_id="001",
            product_name="Стол детский регулируемый", normalized_name="стол детский регулируемый",
            unit="шт.", price=45000, is_group_header=False, raw_data={},
        ),
        MasterProduct(
            upload_id=master_upload.id, source_row=3, external_id="002",
            product_name="Стол ученический регулируемый", normalized_name="стол ученический регулируемый",
            unit="шт.", price=42000, is_group_header=False, raw_data={},
        ),
        # A totally unrelated product - unambiguous for a "chair" query.
        MasterProduct(
            upload_id=master_upload.id, source_row=4, external_id="003",
            product_name="Кресло офисное кожаное премиум", normalized_name="кресло офисное кожаное премиум",
            unit="шт.", price=80000, is_group_header=False, raw_data={},
        ),
        # Filler/noise products - with only a couple of items, RRF's rank
        # positions become structurally identical regardless of true
        # relevance (rank 2 is rank 2 in either scenario). Enough unrelated
        # filler makes the *consistency* of who lands in 2nd place across
        # all three retrieval methods actually differ between the
        # ambiguous and unambiguous queries, which is what RRF responds to.
        MasterProduct(
            upload_id=master_upload.id, source_row=5, external_id="004",
            product_name="Совершенно другой товар", normalized_name="совершенно другой товар",
            unit="шт.", price=1000, is_group_header=False, raw_data={},
        ),
        MasterProduct(
            upload_id=master_upload.id, source_row=6, external_id="005",
            product_name="Шкаф холодильный промышленный", normalized_name="шкаф холодильный промышленный",
            unit="шт.", price=200000, is_group_header=False, raw_data={},
        ),
        MasterProduct(
            upload_id=master_upload.id, source_row=7, external_id="006",
            product_name="Набор инструментов слесарных", normalized_name="набор инструментов слесарных",
            unit="шт.", price=15000, is_group_header=False, raw_data={},
        ),
        MasterProduct(
            upload_id=master_upload.id, source_row=8, external_id="007",
            product_name="Ковер напольный шерстяной", normalized_name="ковер напольный шерстяной",
            unit="шт.", price=25000, is_group_header=False, raw_data={},
        ),
    ]
    dest_products = [
        # Ambiguous: this matches both tables almost equally well.
        DestinationProduct(
            upload_id=dest_upload.id, source_row=2, external_id="D001",
            product_name="Стол регулируемый", normalized_name="стол регулируемый",
            quantity=1, price=44000, status="pending", raw_data={},
        ),
        # Unambiguous: clearly the office chair, nothing else close.
        DestinationProduct(
            upload_id=dest_upload.id, source_row=3, external_id="D002",
            product_name="Кресло офисное кожаное премиум", normalized_name="кресло офисное кожаное премиум",
            quantity=1, price=80000, status="pending", raw_data={},
        ),
    ]
    db_session.add_all(master_products + dest_products)
    db_session.commit()
    return master_upload, dest_upload, master_products, dest_products


def _build_index(db_session):
    records = load_master_records(db_session)
    index = CatalogSearchIndex()
    index.build(records)
    return index


def test_ambiguous_product_has_smaller_margin_than_unambiguous_one(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    index = _build_index(db_session)

    ambiguous_margin = matching.compute_uncertainty_margin(index, dest_products[0])
    unambiguous_margin = matching.compute_uncertainty_margin(index, dest_products[1])

    assert ambiguous_margin is not None
    assert unambiguous_margin is not None
    assert ambiguous_margin < unambiguous_margin


def test_margin_is_none_with_fewer_than_two_candidates(db_session):
    master_upload = Upload(filename="empty_master.xlsx", upload_type="master", status="done")
    dest_upload = Upload(filename="dest.xlsx", upload_type="destination", status="done")
    db_session.add_all([master_upload, dest_upload])
    db_session.flush()

    dp = DestinationProduct(
        upload_id=dest_upload.id, source_row=2, external_id="D001",
        product_name="Что угодно", normalized_name="что угодно",
        quantity=1, price=100, status="pending", raw_data={},
    )
    db_session.add(dp)
    db_session.commit()

    index = CatalogSearchIndex()
    index.build([])  # empty master catalog -> zero candidates for anything

    assert matching.compute_uncertainty_margin(index, dp) is None


def test_prioritize_batch_stores_margins_for_all_pending(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    index = _build_index(db_session)

    result = matching.prioritize_batch(db_session, index, dest_upload.id)
    db_session.commit()

    assert result.checked == 2
    assert result.computed == 2
    assert result.insufficient_candidates == 0

    db_session.refresh(dest_products[0])
    db_session.refresh(dest_products[1])
    assert dest_products[0].uncertainty_margin is not None
    assert dest_products[1].uncertainty_margin is not None


def test_get_next_pending_uncertainty_strategy_surfaces_ambiguous_first(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    index = _build_index(db_session)

    matching.prioritize_batch(db_session, index, dest_upload.id)
    db_session.commit()

    next_dp = matching.get_next_pending(db_session, dest_upload.id, strategy="uncertainty")
    assert next_dp.id == dest_products[0].id  # the ambiguous "стол" product


def test_get_next_pending_sequential_strategy_unaffected_by_margins(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    index = _build_index(db_session)

    matching.prioritize_batch(db_session, index, dest_upload.id)
    db_session.commit()

    next_dp = matching.get_next_pending(db_session, dest_upload.id, strategy="sequential")
    assert next_dp.id == dest_products[0].id  # also first by source_row, coincidentally


def test_get_next_pending_uncertainty_degrades_gracefully_before_prioritize(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    # No prioritize_batch call - uncertainty_margin is NULL for everything.
    next_dp = matching.get_next_pending(db_session, dest_upload.id, strategy="uncertainty")
    assert next_dp.id == dest_products[0].id  # falls back to source_row order
