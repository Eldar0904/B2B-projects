import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import MasterProduct, Upload


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session, Session
    session.close()


@pytest.fixture(autouse=True)
def _isolated_qdrant_storage(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "qdrant_local_path", str(tmp_path / "qdrant"))
    monkeypatch.setattr(settings, "qdrant_url", None)
    monkeypatch.setattr(settings, "embedding_provider", "tfidf")

    import app.services.search.index_manager as index_manager_module

    index_manager_module._singleton = None
    yield
    index_manager_module._singleton = None


def test_rebuild_skips_when_no_master_products(db_session, monkeypatch):
    session, SessionFactory = db_session
    import app.main as main_module

    monkeypatch.setattr(main_module, "SessionLocal", SessionFactory)

    main_module._rebuild_index_if_master_catalog_exists()

    from app.services.search.index_manager import get_index

    assert get_index().is_built is False


def test_rebuild_builds_index_when_master_products_exist(db_session, monkeypatch):
    session, SessionFactory = db_session
    import app.main as main_module

    monkeypatch.setattr(main_module, "SessionLocal", SessionFactory)

    upload = Upload(filename="master.xlsx", upload_type="master", status="done")
    session.add(upload)
    session.flush()
    session.add(
        MasterProduct(
            upload_id=upload.id, source_row=2, external_id="001",
            product_name="Стол детский регулируемый", normalized_name="стол детский регулируемый",
            unit="шт.", price=45000, is_group_header=False, raw_data={},
        )
    )
    session.commit()

    main_module._rebuild_index_if_master_catalog_exists()

    from app.services.search.index_manager import get_index

    index = get_index()
    assert index.is_built is True
    results = index.search("стол детский регулируемый", top_k=1)
    assert len(results) == 1


def test_rebuild_does_not_raise_on_failure(db_session, monkeypatch):
    session, SessionFactory = db_session
    import app.main as main_module

    monkeypatch.setattr(main_module, "SessionLocal", SessionFactory)

    upload = Upload(filename="master.xlsx", upload_type="master", status="done")
    session.add(upload)
    session.flush()
    session.add(
        MasterProduct(
            upload_id=upload.id, source_row=2, external_id="001",
            product_name="Стол", normalized_name="стол",
            unit="шт.", price=1000, is_group_header=False, raw_data={},
        )
    )
    session.commit()

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated Qdrant failure")

    from app.services.search.index_manager import CatalogSearchIndex

    monkeypatch.setattr(CatalogSearchIndex, "build", _boom)

    # Should print a warning and return normally, not raise - a startup
    # failure here must never prevent the whole app from starting.
    main_module._rebuild_index_if_master_catalog_exists()
