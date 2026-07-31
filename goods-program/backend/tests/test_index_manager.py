import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import MasterProduct, Upload
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
    # Point the index manager at an isolated on-disk Qdrant path per test.
    from app.config import settings

    monkeypatch.setattr(settings, "qdrant_local_path", str(tmp_path / "qdrant"))
    monkeypatch.setattr(settings, "qdrant_url", None)
    monkeypatch.setattr(settings, "embedding_provider", "tfidf")


def _seed(db_session):
    upload = Upload(filename="master.xlsx", upload_type="master", status="done")
    db_session.add(upload)
    db_session.flush()

    products = [
        MasterProduct(
            upload_id=upload.id, source_row=2, external_id="001",
            product_name="Стол детский регулируемый", normalized_name="стол детский регулируемый",
            unit="шт.", price=45000, is_group_header=False, raw_data={},
        ),
        MasterProduct(
            upload_id=upload.id, source_row=3, external_id="002",
            product_name="Кресло офисное", normalized_name="кресло офисное",
            unit="шт.", price=30000, is_group_header=False, raw_data={},
        ),
        MasterProduct(
            upload_id=upload.id, source_row=4, external_id="000",
            product_name="Мебель (раздел)", normalized_name="мебель раздел",
            unit=None, price=None, is_group_header=True, raw_data={},
        ),
    ]
    db_session.add_all(products)
    db_session.commit()
    return products


def test_build_from_db_and_search_end_to_end(db_session):
    products = _seed(db_session)
    records = load_master_records(db_session)

    index = CatalogSearchIndex()
    stats = index.build(records)

    assert stats.total_records == 3
    assert stats.indexed_records == 2  # group header excluded
    assert stats.group_headers_excluded == 1

    results = index.search("стол для детей регулируемый", top_k=5)
    assert results[0].master_product_id == products[0].id
    result_ids = [c.master_product_id for c in results]
    assert products[2].id not in result_ids  # group header never a candidate


def test_search_before_build_raises(db_session):
    index = CatalogSearchIndex()
    with pytest.raises(RuntimeError):
        index.search("anything")


def test_search_reranked_returns_reranked_top_k(db_session):
    products = _seed(db_session)
    records = load_master_records(db_session)

    index = CatalogSearchIndex()
    index.build(records)

    reranked = index.search_reranked("стол для детей регулируемый", top_k=2, pool_size=5)
    assert len(reranked) <= 2
    assert reranked[0].master_product_id == products[0].id
    assert reranked[0].reranker_score > 0  # RRF (default) populated it


def test_search_reranked_before_build_raises(db_session):
    index = CatalogSearchIndex()
    with pytest.raises(RuntimeError):
        index.search_reranked("anything")


def test_search_stays_unreranked_for_auto_match_and_raw_endpoints(db_session):
    """Phase 5 auto-matching and the raw /api/search/candidates endpoint
    both call .search() directly and must keep seeing plain final_score
    order, per ARCHITECTURE.md Phase 6 ("reranker_score is not blended
    into... auto-match thresholds").
    """
    products = _seed(db_session)
    records = load_master_records(db_session)

    index = CatalogSearchIndex()
    index.build(records)

    results = index.search("стол для детей регулируемый", top_k=5)
    assert all(c.reranker_score == 0.0 for c in results)


def test_identical_names_score_highly_regardless_of_differing_descriptions(db_session):
    """Real bug found in production: 'Грелка резиновая' (destination) vs.
    an identically-named master product only scored 79% embedding
    similarity because the master side's indexed text included a long
    description while the destination-side query never does (queries are
    always built from normalized_name alone - see matching.py). Fixed by
    making MasterProductRecord.search_text() name-only (see types.py).
    This test locks that fix in: two products sharing a name but with
    very different descriptions must score identically, since description
    text is no longer part of what gets indexed/embedded.
    """
    upload = Upload(filename="master.xlsx", upload_type="master", status="done")
    db_session.add(upload)
    db_session.flush()

    same_name_short_desc = MasterProduct(
        upload_id=upload.id, source_row=2, external_id="A1",
        product_name="Грелка резиновая", normalized_name="грелка резиновая",
        description="Грелка резиновая", unit="шт.", price=4900,
        is_group_header=False, raw_data={},
    )
    same_name_long_desc = MasterProduct(
        upload_id=upload.id, source_row=3, external_id="A2",
        product_name="Грелка резиновая", normalized_name="грелка резиновая",
        description=(
            "Грелка резиновая - это резиновый баллон с объемом 2000 мл "
            "с завинчивающейся крышкой, предназначена для местного "
            "согревания различных участков тела."
        ),
        unit="шт.", price=5200, is_group_header=False, raw_data={},
    )
    db_session.add_all([same_name_short_desc, same_name_long_desc])
    db_session.commit()

    records = load_master_records(db_session)
    index = CatalogSearchIndex()
    index.build(records)

    results = index.search("грелка резиновая", top_k=2)
    scores = {c.master_product_id: c.embedding_score for c in results}

    # Both should now score identically (and highly) since description no
    # longer factors into the indexed text - previously the long-description
    # variant would have scored noticeably lower.
    assert scores[same_name_short_desc.id] == scores[same_name_long_desc.id]
    assert scores[same_name_short_desc.id] > 0.95
