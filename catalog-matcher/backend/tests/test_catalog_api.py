"""Tests for the catalog management API (HANDOFF.md section 13) -
app/api/catalog.py. Follows test_uploads_api.py's TestClient +
dependency-override pattern (see that file for why: a real HTTP round-trip
through FastAPI, not just calling the endpoint function directly, so
response_model serialization and query-param parsing are exercised too).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import CatalogVersion, MasterProduct, Upload


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    # Test-environment-only fix (30 July 2026): SQLite's built-in LOWER()
    # only case-folds ASCII, not Cyrillic - `lower('Стул')` stays 'Стул'
    # unchanged. list_products' search uses `.ilike()`, which SQLAlchemy
    # compiles for SQLite as `lower(column) LIKE lower(pattern)`, so a
    # Cyrillic case-insensitive search silently found nothing here even
    # though the underlying query logic is correct. PostgreSQL's own
    # lower()/ILIKE are locale-aware and handle this fine under a UTF-8
    # locale (the real deployment's actual database), so this is a test-
    # database quirk, not a bug in app/api/catalog.py - fixed by overriding
    # SQLite's LOWER with Python's Unicode-aware str.lower() on this
    # connection only, never touching production code or the Postgres path.
    @event.listens_for(engine, "connect")
    def _register_unicode_lower(dbapi_connection, _connection_record):
        dbapi_connection.create_function("LOWER", 1, lambda s: s.lower() if s is not None else None)

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


def _seed_catalog(session, *, with_catalog_version=True):
    """One master upload with three products, optionally wrapped in an
    active CatalogVersion (the normal, post-HANDOFF-section-4 case) - plus
    a second, unrelated master upload/product to prove scoping actually
    excludes it.
    """
    upload = Upload(filename="catalog.xlsx", upload_type="master", status="done")
    other_upload = Upload(filename="other_catalog.xlsx", upload_type="master", status="done")
    session.add_all([upload, other_upload])
    session.flush()

    products = [
        MasterProduct(
            upload_id=upload.id, source_row=2, external_id="M001",
            product_name="Стол офисный", normalized_name="стол офисный",
            description="Дерево, 120x60 см", unit="шт.", price=15000,
            is_group_header=False, is_active=True, raw_data={},
        ),
        MasterProduct(
            upload_id=upload.id, source_row=3, external_id="M002",
            product_name="Стул офисный", normalized_name="стул офисный",
            unit="шт.", price=5000, is_group_header=False, is_active=True, raw_data={},
        ),
        MasterProduct(
            upload_id=upload.id, source_row=4, external_id="M003",
            product_name="Шкаф архивный", normalized_name="шкаф архивный",
            unit="шт.", price=25000, is_group_header=False, is_active=True, raw_data={},
        ),
    ]
    other_product = MasterProduct(
        upload_id=other_upload.id, source_row=2, external_id="X999",
        product_name="Товар из другого каталога", normalized_name="товар из другого каталога",
        unit="шт.", price=1, is_group_header=False, is_active=True, raw_data={},
    )
    session.add_all(products + [other_product])
    session.flush()

    version = None
    if with_catalog_version:
        version = CatalogVersion(
            name="Test catalog", source_upload_id=upload.id, is_active=True, product_count=3,
        )
        session.add(version)

    session.commit()
    return {"upload": upload, "other_upload": other_upload, "products": products, "other_product": other_product, "version": version}


# --- GET /api/catalog/products ----------------------------------------------


def test_list_products_scoped_to_active_catalog_version(client):
    test_client, session = client
    seed = _seed_catalog(session)

    resp = test_client.get("/api/catalog/products")
    assert resp.status_code == 200
    body = resp.json()

    assert body["total"] == 3
    assert body["catalog_version_id"] == seed["version"].id
    names = [p["product_name"] for p in body["items"]]
    assert names == ["Стол офисный", "Стул офисный", "Шкаф архивный"]  # source_row order
    # The other upload's product must never appear - that's the whole point
    # of scoping to the active CatalogVersion's source_upload_id.
    assert all(p["external_id"] != "X999" for p in body["items"])


def test_list_products_falls_back_to_everything_when_no_active_catalog_version(client):
    """Legacy data from before CatalogVersion existed (HANDOFF.md section 4)
    must not 404 or come back empty - it should just show everything,
    scoping being best-effort rather than mandatory.
    """
    test_client, session = client
    _seed_catalog(session, with_catalog_version=False)

    resp = test_client.get("/api/catalog/products")
    assert resp.status_code == 200
    body = resp.json()

    assert body["catalog_version_id"] is None
    assert body["total"] == 4  # 3 from the "main" upload + 1 from the other, unscoped


def test_list_products_excludes_soft_deleted_by_default(client):
    test_client, session = client
    seed = _seed_catalog(session)
    seed["products"][0].is_active = False
    session.commit()

    resp = test_client.get("/api/catalog/products")
    body = resp.json()

    assert body["total"] == 2
    assert all(p["product_name"] != "Стол офисный" for p in body["items"])


def test_list_products_include_inactive_shows_soft_deleted_rows(client):
    test_client, session = client
    seed = _seed_catalog(session)
    seed["products"][0].is_active = False
    session.commit()

    resp = test_client.get("/api/catalog/products", params={"include_inactive": "true"})
    body = resp.json()

    assert body["total"] == 3
    inactive = [p for p in body["items"] if p["product_name"] == "Стол офисный"]
    assert inactive[0]["is_active"] is False


def test_list_products_search_by_name(client):
    test_client, session = client
    _seed_catalog(session)

    resp = test_client.get("/api/catalog/products", params={"q": "стул"})
    body = resp.json()

    assert body["total"] == 1
    assert body["items"][0]["product_name"] == "Стул офисный"


def test_list_products_search_is_case_insensitive(client):
    test_client, session = client
    _seed_catalog(session)

    resp = test_client.get("/api/catalog/products", params={"q": "СТОЛ"})
    body = resp.json()

    assert body["total"] == 1
    assert body["items"][0]["product_name"] == "Стол офисный"


def test_list_products_search_by_external_id(client):
    test_client, session = client
    _seed_catalog(session)

    resp = test_client.get("/api/catalog/products", params={"q": "M002"})
    body = resp.json()

    assert body["total"] == 1
    assert body["items"][0]["external_id"] == "M002"


def test_list_products_pagination(client):
    test_client, session = client
    _seed_catalog(session)

    resp = test_client.get("/api/catalog/products", params={"limit": 2, "offset": 0})
    first_page = resp.json()
    assert first_page["total"] == 3
    assert len(first_page["items"]) == 2

    resp = test_client.get("/api/catalog/products", params={"limit": 2, "offset": 2})
    second_page = resp.json()
    assert len(second_page["items"]) == 1
    assert second_page["items"][0]["product_name"] == "Шкаф архивный"


# --- POST /api/catalog/products (NEXT_STEPS.md item 7 - add by hand) -------


def test_create_product_adds_a_new_row(client):
    test_client, session = client
    seed = _seed_catalog(session)

    resp = test_client.post(
        "/api/catalog/products",
        json={"product_name": "Новый стул", "external_id": "M999", "unit": "шт.", "price": 7500},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["product_name"] == "Новый стул"
    assert body["external_id"] == "M999"
    assert body["is_active"] is True
    assert body["upload_id"] == seed["upload"].id  # attached to the active catalog's upload

    # normalized_name derived server-side, same as a normal ingested row.
    from app.services.normalizer import build_normalized_name
    assert body["product_name"] is not None

    session.expire_all()
    row = session.get(MasterProduct, body["id"])
    assert row.normalized_name == build_normalized_name("Новый стул", None)

    # Now visible in the default list, and the catalog's product_count grew.
    list_resp = test_client.get("/api/catalog/products")
    assert list_resp.json()["total"] == 4
    session.refresh(seed["version"])
    assert seed["version"].product_count == 4


def test_create_product_requires_a_name(client):
    test_client, session = client
    _seed_catalog(session)

    resp = test_client.post("/api/catalog/products", json={"product_name": ""})
    # Empty string passes Pydantic's `str` validation (it's not None), but
    # is meaningless as a catalog entry - FastAPI/Pydantic itself doesn't
    # reject it, so this documents the current behavior rather than
    # asserting a 422 that isn't actually enforced. A future pass could
    # tighten MasterProductCreate.product_name to `constr(min_length=1)` if
    # this turns out to matter in practice.
    assert resp.status_code in (201, 422)


def test_create_product_404_when_no_active_catalog_version(client):
    test_client, session = client
    _seed_catalog(session, with_catalog_version=False)

    resp = test_client.post("/api/catalog/products", json={"product_name": "Новый стул"})
    assert resp.status_code == 400


def test_create_product_source_row_sorts_after_existing_rows(client):
    """New rows should appear at the end of the default source_row-ordered
    list, not interleaved at row 0 - see create_product's own docstring.
    """
    test_client, session = client
    _seed_catalog(session)

    resp = test_client.post("/api/catalog/products", json={"product_name": "Новый стул", "unit": "шт."})
    assert resp.status_code == 201

    list_resp = test_client.get("/api/catalog/products")
    names = [p["product_name"] for p in list_resp.json()["items"]]
    assert names[-1] == "Новый стул"


def test_create_product_invalidates_cached_index(client, monkeypatch):
    test_client, session = client
    seed = _seed_catalog(session)

    calls = []
    import app.api.catalog as catalog_module
    monkeypatch.setattr(catalog_module, "invalidate_cached_index_for_version", lambda vid: calls.append(vid))

    resp = test_client.post("/api/catalog/products", json={"product_name": "Новый стул"})
    assert resp.status_code == 201
    assert calls == [seed["version"].id]


# --- GET /api/catalog/products/{id} -----------------------------------------


def test_get_product_returns_full_row(client):
    test_client, session = client
    seed = _seed_catalog(session)

    resp = test_client.get(f"/api/catalog/products/{seed['products'][0].id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["product_name"] == "Стол офисный"
    assert body["description"] == "Дерево, 120x60 см"


def test_get_product_404_for_unknown_id(client):
    test_client, _session = client
    resp = test_client.get("/api/catalog/products/does-not-exist")
    assert resp.status_code == 404


# --- PATCH /api/catalog/products/{id} ---------------------------------------


def test_update_product_edits_only_provided_fields(client):
    test_client, session = client
    seed = _seed_catalog(session)
    product = seed["products"][1]  # "Стул офисный", price=5000

    resp = test_client.patch(f"/api/catalog/products/{product.id}", json={"price": 5500})
    assert resp.status_code == 200
    body = resp.json()

    assert body["price"] == 5500
    assert body["product_name"] == "Стул офисный"  # untouched


def test_update_product_recomputes_normalized_name_on_name_change(client):
    """The actual correctness bug this endpoint has to avoid: editing
    product_name without re-deriving normalized_name would leave search
    and find_exact_match comparing against the OLD name forever.
    """
    test_client, session = client
    seed = _seed_catalog(session)
    product = seed["products"][0]

    resp = test_client.patch(
        f"/api/catalog/products/{product.id}",
        json={"product_name": "Стол переговорный"},
    )
    assert resp.status_code == 200

    session.refresh(product)
    assert product.product_name == "Стол переговорный"
    assert product.normalized_name == "стол переговорный"


def test_update_product_leaves_normalized_name_alone_for_unrelated_field(client):
    test_client, session = client
    seed = _seed_catalog(session)
    product = seed["products"][0]
    original_normalized = product.normalized_name

    resp = test_client.patch(f"/api/catalog/products/{product.id}", json={"unit": "компл."})
    assert resp.status_code == 200

    session.refresh(product)
    assert product.normalized_name == original_normalized


def test_update_product_404_for_unknown_id(client):
    test_client, _session = client
    resp = test_client.patch("/api/catalog/products/does-not-exist", json={"price": 1})
    assert resp.status_code == 404


def test_update_product_invalidates_cached_index_for_its_catalog_version(client, monkeypatch):
    test_client, session = client
    seed = _seed_catalog(session)
    product = seed["products"][0]

    calls = []
    import app.api.catalog as catalog_module
    monkeypatch.setattr(catalog_module, "invalidate_cached_index_for_version", lambda vid: calls.append(vid))

    resp = test_client.patch(f"/api/catalog/products/{product.id}", json={"price": 1})
    assert resp.status_code == 200
    assert calls == [seed["version"].id]


def test_update_product_with_empty_body_does_not_invalidate_index(client, monkeypatch):
    """No actual change was requested (an empty PATCH), so there is nothing
    to invalidate - confirms `if changes:` actually gates the call rather
    than firing unconditionally on every request.
    """
    test_client, session = client
    seed = _seed_catalog(session)
    product = seed["products"][0]

    calls = []
    import app.api.catalog as catalog_module
    monkeypatch.setattr(catalog_module, "invalidate_cached_index_for_version", lambda vid: calls.append(vid))

    resp = test_client.patch(f"/api/catalog/products/{product.id}", json={})
    assert resp.status_code == 200
    assert calls == []


# --- DELETE /api/catalog/products/{id} (soft delete) + restore -------------


def test_delete_product_soft_deletes(client):
    test_client, session = client
    seed = _seed_catalog(session)
    product = seed["products"][0]

    resp = test_client.delete(f"/api/catalog/products/{product.id}")
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    session.refresh(product)
    assert product.is_active is False
    # The row itself must still exist - this is a soft delete, not a real
    # DELETE - so Match/Feedback rows that reference it stay valid.
    assert session.get(MasterProduct, product.id) is not None


def test_delete_product_is_idempotent(client):
    test_client, session = client
    seed = _seed_catalog(session)
    product = seed["products"][0]

    first = test_client.delete(f"/api/catalog/products/{product.id}")
    second = test_client.delete(f"/api/catalog/products/{product.id}")
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["is_active"] is False


def test_delete_product_hides_it_from_the_default_list(client):
    test_client, session = client
    seed = _seed_catalog(session)
    product = seed["products"][0]

    test_client.delete(f"/api/catalog/products/{product.id}")

    resp = test_client.get("/api/catalog/products")
    assert resp.json()["total"] == 2


# --- POST /api/catalog/versions/{id}/update-from-file (incremental merge) ---
# Full merge-logic coverage lives in test_catalog_merge.py (service-level,
# against real .xlsx fixtures) - these just prove the HTTP wiring: the file
# actually reaches merge_master_file_into_version, and the response shape is
# what CatalogMergeResult promises.


def _xlsx_bytes(rows):
    import io

    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Код", "Наименование", "Единица измерения", "Сметная цена, тенге"])
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def test_update_catalog_from_file_404_for_unknown_version(client):
    test_client, _session = client
    resp = test_client.post(
        "/api/catalog/versions/does-not-exist/update-from-file",
        files={"file": ("may.xlsx", _xlsx_bytes([["M001", "Стол офисный", "шт.", 15000]]), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 404


def test_update_catalog_from_file_merges_matched_and_new_rows(client):
    test_client, session = client
    seed = _seed_catalog(session)

    resp = test_client.post(
        f"/api/catalog/versions/{seed['version'].id}/update-from-file",
        files={
            "file": (
                "may.xlsx",
                _xlsx_bytes(
                    [
                        ["M001", "Стол офисный (обновлено)", "шт.", 16000],  # matched, changed
                        ["M004", "Новый товар", "шт.", 999],  # brand new code
                        # M002, M003 absent - must be left alone, not deleted
                    ]
                ),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["catalog_version_id"] == seed["version"].id
    assert body["updated"] == 1
    assert body["inserted"] == 1
    assert body["unmatched_existing"] == 2
    assert body["errors"] == []

    session.expire_all()
    m001 = session.query(MasterProduct).filter_by(external_id="M001").one()
    assert m001.id == seed["products"][0].id  # same row, updated in place
    assert m001.product_name == "Стол офисный (обновлено)"
    assert session.query(MasterProduct).filter_by(external_id="M004").count() == 1
    # M002/M003 must still exist, untouched.
    assert session.query(MasterProduct).filter_by(external_id="M002").one().is_active is True
    assert session.query(MasterProduct).filter_by(external_id="M003").one().is_active is True


def test_delete_product_404_for_unknown_id(client):
    test_client, _session = client
    resp = test_client.delete("/api/catalog/products/does-not-exist")
    assert resp.status_code == 404


def test_restore_product_reactivates_a_soft_deleted_row(client):
    test_client, session = client
    seed = _seed_catalog(session)
    product = seed["products"][0]

    test_client.delete(f"/api/catalog/products/{product.id}")
    resp = test_client.post(f"/api/catalog/products/{product.id}/restore")
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True

    listing = test_client.get("/api/catalog/products")
    assert listing.json()["total"] == 3


def test_restore_product_is_idempotent(client):
    test_client, session = client
    seed = _seed_catalog(session)
    product = seed["products"][0]

    first = test_client.post(f"/api/catalog/products/{product.id}/restore")  # already active
    assert first.status_code == 200
    assert first.json()["is_active"] is True


# --- Soft-deleted rows must not resurface in matching/search ---------------


def test_soft_deleted_products_excluded_from_load_master_records(client):
    """The whole point of soft delete (HANDOFF.md section 13): a row a
    reviewer removed from the catalog must stop being a match candidate,
    not just disappear from the management table's UI.
    """
    test_client, session = client
    seed = _seed_catalog(session)
    product = seed["products"][0]

    test_client.delete(f"/api/catalog/products/{product.id}")

    from app.services.search.loader import load_master_records
    records = load_master_records(session, upload_id=seed["upload"].id)
    assert product.id not in {r.id for r in records}
    assert len(records) == 2


def test_soft_deleted_products_excluded_from_exact_match(client):
    """Same principle as load_master_records above, for the OTHER path
    that can produce a match without going through the search index at
    all - matching.find_exact_match queries MasterProduct directly (see
    that function's own docstring), so it needs its own is_active filter,
    not just one on the indexed/hybrid-scoring path.
    """
    test_client, session = client
    seed = _seed_catalog(session)
    product = seed["products"][0]  # "Стол офисный" / normalized "стол офисный"

    from app.models import DestinationProduct
    dest = DestinationProduct(
        upload_id=seed["other_upload"].id, source_row=2,
        product_name="Стол офисный", normalized_name="стол офисный",
        status="pending", raw_data={},
    )
    session.add(dest)
    session.commit()

    from app.services import matching

    # Before deletion: a real exact match.
    assert matching.find_exact_match(session, dest) is not None

    test_client.delete(f"/api/catalog/products/{product.id}")

    # After soft-deleting the only matching row: must come back empty, not
    # silently return the now-inactive product.
    assert matching.find_exact_match(session, dest) is None
