from app.matching.text_similarity import fuzzy_name_score
from app.services.domain import is_medical_item, blocked_product_ids_for_item
from types import SimpleNamespace


def test_fuzzy_name_score_penalises_generic_subset():
    score = fuzzy_name_score("Мебель для ИЗО и Ателье", "Мебель")
    assert score < 0.55


def test_fuzzy_name_score_rubber_ball_not_perfect_for_tourniquet():
    score = fuzzy_name_score("Жгут резиновый", "Мяч резиновый")
    assert score < 0.80


def test_medical_item_detection():
    assert is_medical_item("Грелка резиновая", "")
    assert is_medical_item("Шприцы одноразовые с иглами 5,0", "")
    assert not is_medical_item("Мебель для ИЗО и Ателье", "")


def test_medical_items_block_rubber_ball():
    ball = SimpleNamespace(id=99, category_code="522-101-1000")
    blocked = blocked_product_ids_for_item("Жгут резиновый", "", [ball])
    assert 99 in blocked
