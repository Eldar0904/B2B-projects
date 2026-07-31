import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import DestinationProduct, MasterProduct, Upload
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
    ]
    dp = DestinationProduct(
        upload_id=dest_upload.id, source_row=2, external_id="D001",
        product_name="Стол регулируемый", normalized_name="стол регулируемый",
        quantity=1, price=44000, status="pending", raw_data={},
    )
    session.add_all(master_products + [dp])
    session.commit()

    from app.services.search.loader import load_master_records

    records = load_master_records(session)
    get_index().build(records)

    return dest_upload, dp


def test_prioritize_computes_and_stores_margins(client):
    test_client, session = client
    dest_upload, dp = _seed_and_index(session)

    resp = test_client.post(f"/api/matching/{dest_upload.id}/prioritize")
    assert resp.status_code == 200
    body = resp.json()
    assert body["checked"] == 1
    assert body["computed"] == 1
    assert body["insufficient_candidates"] == 0

    session.refresh(dp)
    assert dp.uncertainty_margin is not None


def test_next_defaults_to_sequential_strategy(client):
    test_client, session = client
    dest_upload, dp = _seed_and_index(session)

    resp = test_client.get(f"/api/matching/{dest_upload.id}/next")
    assert resp.status_code == 200
    assert resp.json()["destination_product_id"] == dp.id


def test_next_accepts_uncertainty_strategy(client):
    test_client, session = client
    dest_upload, dp = _seed_and_index(session)

    test_client.post(f"/api/matching/{dest_upload.id}/prioritize")
    resp = test_client.get(f"/api/matching/{dest_upload.id}/next?strategy=uncertainty")
    assert resp.status_code == 200
    assert resp.json()["destination_product_id"] == dp.id


def test_prioritize_requires_index_built(client):
    test_client, session = client
    resp = test_client.post("/api/matching/nonexistent-upload/prioritize")
    assert resp.status_code == 404
