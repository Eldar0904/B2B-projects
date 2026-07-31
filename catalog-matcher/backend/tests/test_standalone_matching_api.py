"""HTTP-level tests for the two new endpoints in the "no resume" fix
(NEXT_STEPS.md item 6): GET /destinations/resumable and POST /decisions.
Follows test_catalog_api.py's TestClient + dependency-override pattern -
full service-level coverage (the actual resume/persist logic) lives in
test_standalone_matching.py; these just prove the HTTP wiring.

`/excel/run/start`'s resume mode (destination_upload_id) is deliberately
NOT tested here via TestClient - this project has no established pattern
for exercising multipart file-upload endpoints through a real HTTP
round-trip (see test_uploads_api.py, which only tests the non-upload GET
endpoints for the same reason); it's already covered end-to-end at the
service layer in test_standalone_matching.py.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import CatalogVersion, DestinationProduct, MasterProduct, Match, Upload


@pytest.fixture(autouse=True)
def _isolated_search_infra(tmp_path, monkeypatch):
    """Only the new /manual-search tests below actually build a real
    search index - but this is autouse so it's impossible for a future
    test added to this file to forget it and silently touch the real
    default Qdrant/index-cache paths.
    """
    from app.config import settings
    from app.services.search import vector_search as vector_search_module

    monkeypatch.setattr(settings, "qdrant_local_path", str(tmp_path / "qdrant"))
    monkeypatch.setattr(settings, "qdrant_url", None)
    monkeypatch.setattr(settings, "embedding_provider", "tfidf")
    monkeypatch.setattr(vector_search_module, "_CACHE_DIR", tmp_path / ".index_cache")


@pytest.fixture
def client():
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

    session = TestSession()
    yield TestClient(app), session
    session.close()
    app.dependency_overrides.clear()


def _seed_destination(session, *, decided=False):
    upload = Upload(filename="dest.xlsx", upload_type="destination", status="done")
    session.add(upload)
    session.flush()
    dp = DestinationProduct(
        upload_id=upload.id, source_row=2, product_name="Стул офисный",
        normalized_name="стул офисный", status="matched" if decided else "pending",
        raw_data={},
    )
    session.add(dp)
    session.commit()
    return upload, dp


def test_destinations_resumable_reports_pending_count(client):
    test_client, session = client
    upload, dp = _seed_destination(session, decided=False)

    resp = test_client.get("/api/v1/matching/destinations/resumable")
    assert resp.status_code == 200
    body = resp.json()
    entry = next(d for d in body["destinations"] if d["upload_id"] == upload.id)
    assert entry["total"] == 1
    assert entry["pending"] == 1
    assert entry["decided"] == 0


def test_destinations_resumable_empty_when_no_destination_uploads(client):
    test_client, _session = client
    resp = test_client.get("/api/v1/matching/destinations/resumable")
    assert resp.status_code == 200
    assert resp.json()["destinations"] == []


def test_save_decisions_persists_immediately_and_returns_no_file(client):
    test_client, session = client
    master_upload = Upload(filename="m.xlsx", upload_type="master", status="done")
    session.add(master_upload)
    session.flush()
    master_product = MasterProduct(
        upload_id=master_upload.id, source_row=2, external_id="M1",
        product_name="Стол офисный", normalized_name="стол офисный",
        unit="шт.", price=1000, is_group_header=False, raw_data={},
    )
    session.add(master_product)
    session.flush()
    _upload, dp = _seed_destination(session, decided=False)
    session.commit()

    resp = test_client.post(
        "/api/v1/matching/decisions",
        json={
            "rows": [
                {
                    "destination_id": dp.id,
                    "catalog_id": master_product.id,
                    "score": 0.91,
                    "decision": "вручную",
                }
            ],
            "catalog_version_id": None,
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"saved": 1}
    # No file attachment - unlike /save, this must not return a workbook.
    assert "attachment" not in resp.headers.get("content-disposition", "")

    session.expire_all()
    refreshed = session.get(DestinationProduct, dp.id)
    assert refreshed.status == "matched"
    assert session.query(Match).filter_by(destination_product_id=dp.id).count() == 1


def test_save_decisions_then_save_does_not_double_write(client):
    """The actual property Part A depends on: a decision sent to /decisions
    and then included again in the final /save (already_persisted=True)
    must not create a second Match row.
    """
    test_client, session = client
    master_upload = Upload(filename="m.xlsx", upload_type="master", status="done")
    session.add(master_upload)
    session.flush()
    master_product = MasterProduct(
        upload_id=master_upload.id, source_row=2, external_id="M1",
        product_name="Стол офисный", normalized_name="стол офисный",
        unit="шт.", price=1000, is_group_header=False, raw_data={},
    )
    session.add(master_product)
    session.flush()
    _upload, dp = _seed_destination(session, decided=False)
    session.commit()

    row = {
        "destination_id": dp.id,
        "catalog_id": master_product.id,
        "score": 0.91,
        "decision": "вручную",
    }
    resp1 = test_client.post(
        "/api/v1/matching/decisions", json={"rows": [row], "catalog_version_id": None}
    )
    assert resp1.status_code == 200

    row_already_persisted = {**row, "already_persisted": True}
    resp2 = test_client.post(
        "/api/v1/matching/save",
        json={"rows": [row_already_persisted], "catalog_version_id": None},
    )
    assert resp2.status_code == 200

    assert session.query(Match).filter_by(destination_product_id=dp.id).count() == 1


# --- POST /api/v1/matching/manual-search (NEXT_STEPS.md item 6) ------------


def _seed_catalog_version(session):
    upload = Upload(filename="catalog.xlsx", upload_type="master", status="done")
    session.add(upload)
    session.flush()
    product = MasterProduct(
        upload_id=upload.id, source_row=2, external_id="M1",
        product_name="Стол офисный регулируемый", normalized_name="стол офисный регулируемый",
        unit="шт.", price=15000, is_group_header=False, raw_data={},
    )
    session.add(product)
    session.flush()
    version = CatalogVersion(name="Test catalog", source_upload_id=upload.id, is_active=True, product_count=1)
    session.add(version)
    session.commit()
    return version, product


def test_manual_search_returns_candidates(client):
    test_client, session = client
    version, product = _seed_catalog_version(session)

    resp = test_client.post(
        "/api/v1/matching/manual-search",
        json={"query": "стол офисный", "catalog_version_id": version.id, "top_k": 5},
    )
    assert resp.status_code == 200
    candidates = resp.json()["candidates"]
    assert any(c["catalog_id"] == product.id for c in candidates)


def test_manual_search_404_for_unknown_catalog_version(client):
    test_client, _session = client
    resp = test_client.post(
        "/api/v1/matching/manual-search",
        json={"query": "стол", "catalog_version_id": "does-not-exist"},
    )
    assert resp.status_code == 404


def test_manual_search_400_when_catalog_version_id_missing(client):
    test_client, _session = client
    resp = test_client.post("/api/v1/matching/manual-search", json={"query": "стол"})
    assert resp.status_code == 400


def test_manual_search_empty_query_returns_no_candidates_not_an_error(client):
    test_client, session = client
    version, _product = _seed_catalog_version(session)

    resp = test_client.post(
        "/api/v1/matching/manual-search",
        json={"query": "   ", "catalog_version_id": version.id},
    )
    assert resp.status_code == 200
    assert resp.json()["candidates"] == []
