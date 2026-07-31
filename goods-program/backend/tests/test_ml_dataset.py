import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import DestinationProduct, Feedback, MasterProduct, Upload
from app.services.ml.dataset import build_training_pairs
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
        MasterProduct(
            upload_id=master_upload.id, source_row=4, external_id="003",
            product_name="Кресло офисное", normalized_name="кресло офисное",
            unit="шт.", price=30000, is_group_header=False, raw_data={},
        ),
    ]
    dest_products = [
        DestinationProduct(
            upload_id=dest_upload.id, source_row=2, external_id="D001",
            product_name="Стол для детей регулируемый", normalized_name="стол для детей регулируемый",
            quantity=10, price=45000, status="matched", raw_data={},
        ),
        DestinationProduct(
            upload_id=dest_upload.id, source_row=3, external_id="D002",
            product_name="Что-то совсем другое", normalized_name="что то совсем другое",
            quantity=1, price=999, status="no_match", raw_data={},
        ),
    ]
    db_session.add_all(master_products + dest_products)
    db_session.flush()

    # Feedback 1: user_selected master_products[0] out of a shown top-3.
    feedback1 = Feedback(
        destination_product_id=dest_products[0].id,
        selected_master_product_id=master_products[0].id,
        decision_type="user_selected",
        candidate_data={
            "destination_product": dest_products[0].product_name,
            "selected_master_product": master_products[0].product_name,
            "candidates": [
                {"id": master_products[0].id, "rank": 1, "score": 0.9},
                {"id": master_products[1].id, "rank": 2, "score": 0.7},
                {"id": master_products[2].id, "rank": 3, "score": 0.1},
            ],
            "decision": "user_selected",
        },
    )
    # Feedback 2: no_match - all shown candidates are negatives.
    feedback2 = Feedback(
        destination_product_id=dest_products[1].id,
        selected_master_product_id=None,
        decision_type="no_match",
        candidate_data={
            "destination_product": dest_products[1].product_name,
            "selected_master_product": None,
            "candidates": [
                {"id": master_products[2].id, "rank": 1, "score": 0.3},
            ],
            "decision": "no_match",
        },
    )
    db_session.add_all([feedback1, feedback2])
    db_session.commit()
    return master_upload, dest_upload, master_products, dest_products


def _build_index(db_session):
    records = load_master_records(db_session)
    index = CatalogSearchIndex()
    index.build(records)
    return index


def test_positive_and_hard_negative_pairs_extracted(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    index = _build_index(db_session)

    pairs = build_training_pairs(db_session, index)

    # 3 candidates from feedback1 + 1 candidate from feedback2 = 4 pairs total.
    assert len(pairs) == 4

    positives = [p for p in pairs if p.label == 1]
    negatives = [p for p in pairs if p.label == 0]
    assert len(positives) == 1
    assert positives[0].master_product_id == master_products[0].id
    assert positives[0].source_decision_type == "user_selected"

    # The hard negative from feedback1 (shown, not picked) and the
    # no_match candidate from feedback2 should both be label=0.
    negative_master_ids = {p.master_product_id for p in negatives}
    assert master_products[1].id in negative_master_ids  # shown but not selected
    assert master_products[2].id in negative_master_ids  # appears in both feedbacks


def test_no_match_feedback_produces_only_negatives(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    index = _build_index(db_session)

    pairs = build_training_pairs(db_session, index)
    no_match_pairs = [p for p in pairs if p.source_decision_type == "no_match"]
    assert len(no_match_pairs) == 1
    assert no_match_pairs[0].label == 0


def test_scoped_to_upload_id(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    index = _build_index(db_session)

    pairs = build_training_pairs(db_session, index, upload_id=dest_upload.id)
    assert len(pairs) == 4  # both feedback rows belong to this one destination upload

    other_upload_pairs = build_training_pairs(db_session, index, upload_id="nonexistent")
    assert other_upload_pairs == []


def test_features_are_populated_for_each_pair(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    index = _build_index(db_session)

    pairs = build_training_pairs(db_session, index)
    for p in pairs:
        vec = p.features.as_list()
        assert len(vec) == 5
        assert all(isinstance(v, float) for v in vec)
