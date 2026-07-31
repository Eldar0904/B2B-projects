import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import DestinationProduct, Feedback, MasterProduct, Upload
from app.services.search.index_manager import get_index


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "qdrant_local_path", str(tmp_path / "qdrant"))
    monkeypatch.setattr(settings, "qdrant_url", None)
    monkeypatch.setattr(settings, "embedding_provider", "tfidf")

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def _override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db

    # Reset the process-wide search index singleton between tests.
    import app.services.search.index_manager as index_manager_module

    index_manager_module._singleton = None

    session = TestSession()
    yield TestClient(app), session
    session.close()
    app.dependency_overrides.clear()


def _seed_and_index(session):
    master_upload = Upload(filename="master.xlsx", upload_type="master", status="done")
    dest_upload = Upload(filename="dest.xlsx", upload_type="destination", status="done")
    session.add_all([master_upload, dest_upload])
    session.flush()

    mp = MasterProduct(
        upload_id=master_upload.id, source_row=2, external_id="001",
        product_name="Стол детский регулируемый", normalized_name="стол детский регулируемый",
        unit="шт.", price=45000, is_group_header=False, raw_data={},
    )
    dp = DestinationProduct(
        upload_id=dest_upload.id, source_row=2, external_id="D001",
        product_name="Стол для детей регулируемый", normalized_name="стол для детей регулируемый",
        quantity=10, price=45000, status="matched", raw_data={},
    )
    session.add_all([mp, dp])
    session.flush()

    feedback = Feedback(
        destination_product_id=dp.id,
        selected_master_product_id=mp.id,
        decision_type="user_selected",
        candidate_data={
            "destination_product": dp.product_name,
            "selected_master_product": mp.product_name,
            "candidates": [{"id": mp.id, "rank": 1, "score": 0.9}],
            "decision": "user_selected",
        },
    )
    session.add(feedback)
    session.commit()

    from app.services.search.loader import load_master_records

    records = load_master_records(session)
    get_index().build(records)


def test_training_readiness_reports_shortfall(client):
    test_client, session = client
    _seed_and_index(session)

    resp = test_client.get("/api/ml/training-readiness")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_examples"] == 1
    assert body["positive_examples"] == 1
    assert body["ready"] is False
    assert body["examples_needed"] == body["min_required"] - 1


def test_training_readiness_requires_index_built(client):
    test_client, session = client
    resp = test_client.get("/api/ml/training-readiness")
    assert resp.status_code == 409


def test_train_below_threshold_does_not_deploy(client):
    test_client, session = client
    _seed_and_index(session)

    resp = test_client.post("/api/ml/train")
    assert resp.status_code == 200
    body = resp.json()
    assert body["should_deploy"] is False
    assert body["model_auc"] is None
    assert "500" in body["reason"] or str(body["n_total"]) in body["reason"]
