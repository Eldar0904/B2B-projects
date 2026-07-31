import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import DestinationProduct, Feedback, MasterProduct, Match, Upload
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
        MasterProduct(
            upload_id=master_upload.id, source_row=2, external_id="001",
            product_name="Стол детский регулируемый", normalized_name="стол детский регулируемый",
            unit="шт.", price=45000, is_group_header=False, raw_data={},
        ),
        MasterProduct(
            upload_id=master_upload.id, source_row=3, external_id="002",
            product_name="Кресло офисное", normalized_name="кресло офисное",
            unit="шт.", price=30000, is_group_header=False, raw_data={},
        ),
    ]
    dest_products = [
        DestinationProduct(
            upload_id=dest_upload.id, source_row=2, external_id="D001",
            product_name="Стол для детей регулируемый", normalized_name="стол для детей регулируемый",
            quantity=10, price=45000, status="pending", raw_data={},
        ),
        DestinationProduct(
            upload_id=dest_upload.id, source_row=3, external_id="D002",
            product_name="Кресло офис", normalized_name="кресло офис",
            quantity=5, price=30000, status="pending", raw_data={},
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


def test_progress_counts_by_status(db_session):
    master_upload, dest_upload, _, dest_products = _seed(db_session)
    progress = matching.get_progress(db_session, dest_upload.id)
    assert progress.total == 2
    assert progress.pending == 2
    assert progress.matched == 0
    assert progress.no_match == 0

    dest_products[0].status = "matched"
    db_session.commit()
    progress = matching.get_progress(db_session, dest_upload.id)
    assert progress.pending == 1
    assert progress.matched == 1


def test_get_next_pending_returns_lowest_source_row(db_session):
    master_upload, dest_upload, _, dest_products = _seed(db_session)
    dest_products[0].status = "matched"
    db_session.commit()

    next_dp = matching.get_next_pending(db_session, dest_upload.id)
    assert next_dp.id == dest_products[1].id


def test_get_next_pending_returns_none_when_all_done(db_session):
    master_upload, dest_upload, _, dest_products = _seed(db_session)
    for dp in dest_products:
        dp.status = "matched"
    db_session.commit()

    assert matching.get_next_pending(db_session, dest_upload.id) is None


def test_top_candidates_match_correct_master_product(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    index = _build_index(db_session)

    candidates = matching.get_top_candidates(db_session, index, dest_products[0], top_k=3)
    assert candidates[0].master_product.id == master_products[0].id
    assert len(candidates[0].explanation) > 0


def test_confirm_creates_match_and_updates_status(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    index = _build_index(db_session)

    candidates = [c.candidate for c in matching.get_top_candidates(db_session, index, dest_products[0], top_k=3)]
    match = matching.confirm_match(db_session, dest_products[0], master_products[0].id, rank=1, candidates=candidates)
    db_session.commit()

    assert dest_products[0].status == "matched"
    stored = db_session.get(Match, match.id)
    assert stored.master_product_id == master_products[0].id
    assert stored.is_confirmed is True
    assert stored.confidence is not None


def test_reject_sets_no_match_status(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    matching.reject_match(db_session, dest_products[1])
    db_session.commit()
    assert dest_products[1].status == "no_match"


def test_explanation_reflects_real_scores_not_fake():
    from app.services.search.types import ScoredCandidate

    high_conf = ScoredCandidate(
        master_product_id="1", embedding_score=0.9, keyword_score=1.0,
        fuzzy_name_score=0.95, matched_by={"keyword", "fuzzy", "vector"},
    )
    reasons = matching.build_explanation(high_conf)
    assert any("Exact keyword" in r for r in reasons)
    assert any("Found by 3 of 3" in r for r in reasons)

    low_conf = ScoredCandidate(master_product_id="2")
    reasons_low = matching.build_explanation(low_conf)
    assert "Low-confidence candidate; no strong signal from any retrieval method" in reasons_low


def test_confirm_creates_feedback_with_user_selected_decision(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    index = _build_index(db_session)

    candidates = [c.candidate for c in matching.get_top_candidates(db_session, index, dest_products[0], top_k=3)]
    matching.confirm_match(db_session, dest_products[0], master_products[0].id, rank=1, candidates=candidates)
    db_session.commit()

    feedback = db_session.query(Feedback).filter_by(destination_product_id=dest_products[0].id).one()
    assert feedback.decision_type == "user_selected"
    assert feedback.selected_master_product_id == master_products[0].id
    assert feedback.candidate_data["decision"] == "user_selected"
    assert feedback.candidate_data["selected_master_product"] == master_products[0].product_name
    assert feedback.candidate_data["destination_product"] == dest_products[0].product_name
    assert len(feedback.candidate_data["candidates"]) >= 1
    assert feedback.candidate_data["candidates"][0]["rank"] == 1


def test_confirm_with_rank_zero_creates_manual_search_selected_decision(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    index = _build_index(db_session)

    candidates = [c.candidate for c in matching.get_top_candidates(db_session, index, dest_products[1], top_k=3)]
    matching.confirm_match(db_session, dest_products[1], master_products[1].id, rank=0, candidates=candidates)
    db_session.commit()

    feedback = db_session.query(Feedback).filter_by(destination_product_id=dest_products[1].id).one()
    assert feedback.decision_type == "manual_search_selected"
    assert feedback.selected_master_product_id == master_products[1].id


def test_reject_creates_feedback_with_no_match_decision_and_shown_candidates(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    index = _build_index(db_session)

    shown = [c.candidate for c in matching.get_top_candidates(db_session, index, dest_products[1], top_k=3)]
    matching.reject_match(db_session, dest_products[1], shown)
    db_session.commit()

    feedback = db_session.query(Feedback).filter_by(destination_product_id=dest_products[1].id).one()
    assert feedback.decision_type == "no_match"
    assert feedback.selected_master_product_id is None
    assert feedback.candidate_data["selected_master_product"] is None
    assert len(feedback.candidate_data["candidates"]) == len(shown)


def test_reject_with_no_candidates_still_records_feedback(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    matching.reject_match(db_session, dest_products[1])
    db_session.commit()

    feedback = db_session.query(Feedback).filter_by(destination_product_id=dest_products[1].id).one()
    assert feedback.decision_type == "no_match"
    assert feedback.candidate_data["candidates"] == []


def test_feedback_stats_counts_by_decision_type(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    index = _build_index(db_session)

    candidates0 = [c.candidate for c in matching.get_top_candidates(db_session, index, dest_products[0], top_k=3)]
    matching.confirm_match(db_session, dest_products[0], master_products[0].id, rank=1, candidates=candidates0)
    matching.reject_match(db_session, dest_products[1])
    db_session.commit()

    stats = matching.get_feedback_stats(db_session, dest_upload.id)
    assert stats.total == 2
    assert stats.user_selected == 1
    assert stats.no_match == 1
    assert stats.manual_search_selected == 0
    assert stats.auto_accepted == 0
