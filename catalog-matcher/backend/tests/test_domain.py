from app.matching.base import Candidate
from app.services.domain import (
    blocked_product_ids_for_item,
    catalog_product_is_medical,
    filter_medical_candidates,
    is_medical_item,
)
from types import SimpleNamespace


def _product(name, code="x-1", category_code="522-101-1000"):
    return SimpleNamespace(id=1, name=name, code=code, category_code=category_code)


def test_syringe_item_not_medical_product_physics_kit():
    kit = _product(
        "Набор демонстрационный для изучения механики",
        category_code="521-202-0600",
    )
    assert not catalog_product_is_medical(kit)


def test_syringe_item_blocks_board_and_ball():
    board = _product("Доска наклонная навесная 1,5м", category_code="522-101-1000")
    ball = _product("Мяч резиновый", category_code="522-101-1000")
    products = [board, ball]
    blocked = blocked_product_ids_for_item("Шприцы одноразовые с иглами 5,0", "", products)
    assert 1 in blocked  # both non-medical names


def test_medical_filter_removes_all_when_no_catalog_match():
    board = _product("Доска наклонная", category_code="522-101-1000")
    lookup = {1: board}
    cands = [Candidate(catalog_product_id=1, score=0.82, explanation="bad")]
    result = filter_medical_candidates(
        "Шприцы одноразовые", "", cands, lookup,
    )
    assert result == []


def test_rubber_ball_blocked_for_tourniquet():
    ball = _product("Мяч резиновый", category_code="522-101-1000")
    blocked = blocked_product_ids_for_item("Жгут резиновый", "", [ball])
    assert ball.id in blocked
