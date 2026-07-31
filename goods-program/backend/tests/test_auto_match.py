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
        # exact normalized-name match against master_products[0]
        DestinationProduct(
            upload_id=dest_upload.id, source_row=2, external_id=None,
            product_name="Стол детский регулируемый", normalized_name="стол детский регулируемый",
            quantity=10, price=45000, status="pending", raw_data={},
        ),
        # exact external_id match against master_products[1]
        DestinationProduct(
            upload_id=dest_upload.id, source_row=3, external_id="002",
            product_name="Кресло для офиса (другое название)", normalized_name="кресло для офиса другое название",
            quantity=5, price=30000, status="pending", raw_data={},
        ),
        # no exact match and not similar enough for threshold auto-accept
        DestinationProduct(
            upload_id=dest_upload.id, source_row=4, external_id=None,
            product_name="Совершенно другой товар xyz", normalized_name="совершенно другой товар xyz",
            quantity=1, price=1000, status="pending", raw_data={},
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


def test_find_exact_match_by_normalized_name(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    found = matching.find_exact_match(db_session, dest_products[0])
    assert found is not None
    assert found.id == master_products[0].id


def test_find_exact_match_by_external_id(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    found = matching.find_exact_match(db_session, dest_products[1])
    assert found is not None
    assert found.id == master_products[1].id


def test_find_exact_match_returns_none_when_no_match(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    found = matching.find_exact_match(db_session, dest_products[2])
    assert found is None


def test_classify_confidence_levels():
    assert matching.classify_confidence(0.96, high=0.95, medium=0.70) == "high"
    assert matching.classify_confidence(0.95, high=0.95, medium=0.70) == "high"
    assert matching.classify_confidence(0.80, high=0.95, medium=0.70) == "medium"
    assert matching.classify_confidence(0.70, high=0.95, medium=0.70) == "medium"
    assert matching.classify_confidence(0.50, high=0.95, medium=0.70) == "low"


def test_auto_accept_match_creates_match_and_auto_accepted_feedback(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    match = matching.auto_accept_match(
        db_session, dest_products[0], master_products[0], confidence=0.99, method="exact_match"
    )
    db_session.commit()

    assert dest_products[0].status == "matched"
    stored_match = db_session.get(Match, match.id)
    assert stored_match.match_type == "auto_accepted"
    assert stored_match.method == "exact_match"
    assert float(stored_match.confidence) == 0.99

    feedback = db_session.query(Feedback).filter_by(destination_product_id=dest_products[0].id).one()
    assert feedback.decision_type == "auto_accepted"
    assert feedback.selected_master_product_id == master_products[0].id


def test_try_auto_match_uses_exact_match_first(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    index = _build_index(db_session)

    match = matching.try_auto_match(db_session, index, dest_products[0], high_threshold=0.95)
    db_session.commit()
    assert match is not None
    assert match.method == "exact_match"
    assert dest_products[0].status == "matched"


def test_try_auto_match_returns_none_when_nothing_qualifies(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    index = _build_index(db_session)

    match = matching.try_auto_match(db_session, index, dest_products[2], high_threshold=0.95)
    db_session.commit()
    assert match is None
    assert dest_products[2].status == "pending"  # left for human review


def test_try_auto_match_can_use_threshold_path_with_lowered_threshold(db_session):
    """With the spec's literal 0.95 default, only exact matches fire (see
    ARCHITECTURE.md Phase 5). Lowering the threshold here proves the
    threshold-based path itself works, independent of that default.
    """
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    index = _build_index(db_session)

    # dest_products[2] has no exact match; with a low enough threshold,
    # whatever the hybrid search ranks first should auto-accept.
    match = matching.try_auto_match(db_session, index, dest_products[2], high_threshold=0.0)
    db_session.commit()
    assert match is not None
    assert match.method == "auto_threshold"


def test_run_auto_match_batch_leaves_non_qualifying_pending(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    index = _build_index(db_session)

    result = matching.run_auto_match_batch(db_session, index, dest_upload.id, high_threshold=0.95)
    db_session.commit()

    assert result.checked == 3
    assert result.exact_matches == 2
    assert result.threshold_matches == 0
    assert result.auto_matched == 2
    assert result.still_pending == 1

    progress = matching.get_progress(db_session, dest_upload.id)
    assert progress.matched == 2
    assert progress.pending == 1


def test_auto_reject_match_creates_no_match_feedback(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)

    feedback = matching.auto_reject_match(db_session, dest_products[2], top_score=0.05)
    db_session.commit()

    assert dest_products[2].status == "no_match"
    assert feedback.decision_type == "auto_rejected"
    assert feedback.selected_master_product_id is None
    assert feedback.candidate_data["decision"] == "auto_rejected"


def test_auto_reject_match_handles_zero_candidates(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)

    feedback = matching.auto_reject_match(db_session, dest_products[2], top_score=None)
    db_session.commit()

    assert dest_products[2].status == "no_match"
    assert feedback.candidate_data["candidates"] == []


def test_try_auto_match_leaves_pending_when_low_threshold_not_set(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    index = _build_index(db_session)

    match = matching.try_auto_match(db_session, index, dest_products[2], high_threshold=0.95, low_threshold=None)
    db_session.commit()
    assert match is None
    assert dest_products[2].status == "pending"


def test_try_auto_match_auto_rejects_below_low_threshold(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    index = _build_index(db_session)

    # A very high low_threshold guarantees the top candidate's score
    # (whatever it is) falls below it, forcing the auto-reject path.
    match = matching.try_auto_match(db_session, index, dest_products[2], high_threshold=0.95, low_threshold=1.0)
    db_session.commit()
    assert match is None  # no Match created - this was a rejection, not an acceptance
    assert dest_products[2].status == "no_match"

    feedback = db_session.query(Feedback).filter_by(destination_product_id=dest_products[2].id).one()
    assert feedback.decision_type == "auto_rejected"


def test_run_auto_match_batch_with_low_threshold_clears_the_queue(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    index = _build_index(db_session)

    result = matching.run_auto_match_batch(
        db_session, index, dest_upload.id, high_threshold=0.95, low_threshold=1.0
    )
    db_session.commit()

    assert result.checked == 3
    assert result.exact_matches == 2
    assert result.threshold_matches == 0
    assert result.auto_rejected == 1
    assert result.still_pending == 0  # nothing left needing human review

    progress = matching.get_progress(db_session, dest_upload.id)
    assert progress.matched == 2
    assert progress.no_match == 1
    assert progress.pending == 0


def test_run_auto_match_batch_without_low_threshold_matches_old_behavior(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)
    index = _build_index(db_session)

    result = matching.run_auto_match_batch(db_session, index, dest_upload.id, high_threshold=0.95)
    db_session.commit()

    assert result.auto_rejected == 0
    assert result.still_pending == 1  # dest_products[2] stays pending, as before this feature existed


def test_feedback_stats_counts_auto_rejected_separately_from_no_match(db_session):
    master_upload, dest_upload, master_products, dest_products = _seed(db_session)

    matching.auto_reject_match(db_session, dest_products[2], top_score=0.05)
    db_session.commit()

    stats = matching.get_feedback_stats(db_session, dest_upload.id)
    assert stats.auto_rejected == 1
    assert stats.no_match == 0  # a human never clicked "None of these" here
