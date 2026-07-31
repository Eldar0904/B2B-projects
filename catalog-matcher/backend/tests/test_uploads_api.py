from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Upload


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


def _seed_uploads(session):
    base = datetime.now(timezone.utc)

    master_old = Upload(
        filename="master_old.xlsx",
        upload_type="master",
        status="done",
        created_at=base - timedelta(hours=3),
    )
    dest_old = Upload(
        filename="dest_old.xlsx",
        upload_type="destination",
        status="done",
        created_at=base - timedelta(hours=2),
    )
    master_new = Upload(
        filename="master_new.xlsx",
        upload_type="master",
        status="processing",
        created_at=base - timedelta(hours=1),
    )
    dest_new = Upload(
        filename="dest_new.xlsx",
        upload_type="destination",
        status="pending",
        created_at=base,
    )

    session.add_all([master_old, dest_old, master_new, dest_new])
    session.commit()

    return {
        "master_old": master_old,
        "dest_old": dest_old,
        "master_new": master_new,
        "dest_new": dest_new,
    }


def test_list_uploads_returns_all_ordered_most_recent_first(client):
    test_client, session = client
    uploads = _seed_uploads(session)

    resp = test_client.get("/api/uploads")
    assert resp.status_code == 200
    body = resp.json()

    assert len(body) == 4
    filenames = [u["filename"] for u in body]
    assert filenames == [
        uploads["dest_new"].filename,
        uploads["master_new"].filename,
        uploads["dest_old"].filename,
        uploads["master_old"].filename,
    ]

    # created_at isn't exposed in the UploadStatus response schema, so we
    # confirm ordering via id/filename against the known insertion order
    # (dest_new is most recent, master_old is least recent).
    assert body[0]["id"] == uploads["dest_new"].id
    assert body[-1]["id"] == uploads["master_old"].id


def test_list_uploads_filters_destination(client):
    test_client, session = client
    uploads = _seed_uploads(session)

    resp = test_client.get("/api/uploads", params={"upload_type": "destination"})
    assert resp.status_code == 200
    body = resp.json()

    assert len(body) == 2
    assert all(u["upload_type"] == "destination" for u in body)
    filenames = [u["filename"] for u in body]
    assert filenames == [uploads["dest_new"].filename, uploads["dest_old"].filename]


def test_list_uploads_filters_master(client):
    test_client, session = client
    uploads = _seed_uploads(session)

    resp = test_client.get("/api/uploads", params={"upload_type": "master"})
    assert resp.status_code == 200
    body = resp.json()

    assert len(body) == 2
    assert all(u["upload_type"] == "master" for u in body)
    filenames = [u["filename"] for u in body]
    assert filenames == [uploads["master_new"].filename, uploads["master_old"].filename]


def test_list_uploads_empty_database_returns_empty_list(client):
    test_client, _session = client

    resp = test_client.get("/api/uploads")
    assert resp.status_code == 200
    assert resp.json() == []
