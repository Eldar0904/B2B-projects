from types import SimpleNamespace

from app.services.category import (
    extract_category_code,
    is_category_header_code,
    build_category_name_map,
    infer_item_category,
    apply_catalog_categories,
    resolve_category_from_label,
    looks_like_catalog_code,
)


def test_extract_category_code_from_government_code():
    assert extract_category_code("521-201-0501") == "521-201-0500"
    assert extract_category_code("521-201-0709") == "521-201-0700"
    assert extract_category_code("522-101-1090-0002") == "522-101-1000"
    assert extract_category_code("") is None


def test_category_header_detection():
    assert is_category_header_code("521-201-0500") is True
    assert is_category_header_code("521-201-0501") is False


def test_build_category_name_map_uses_header_rows():
    products = [
        SimpleNamespace(code="521-201-0500", name='Предмет "Музыка"'),
        SimpleNamespace(code="521-201-0501", name="Пианино"),
        SimpleNamespace(code="521-402-0100", name="Зал лечебной физкультуры"),
    ]
    name_map = build_category_name_map(products)
    assert name_map["521-201-0500"] == 'Предмет "Музыка"'
    assert name_map["521-402-0100"] == "Зал лечебной физкультуры"


def test_infer_item_category_for_piano():
    name_map = {"521-201-0500": 'Предмет "Музыка"'}
    code, name, note = infer_item_category("Рояль (170 см)", "", name_map)
    assert code == "521-201-0500"
    assert "Музыка" in (name or "")
    assert "inferred" in note


def test_infer_item_category_for_gym_mat():
    name_map = {"521-402-0100": "Зал лечебной физкультуры"}
    code, _, _ = infer_item_category("Мат напольный 200x100", "", name_map)
    assert code == "521-402-0100"


def test_resolve_category_from_user_label():
    code, _, note = resolve_category_from_label("Мебель", {})
    assert code == "521-208-0100"
    assert "label" in note

    code, _, _ = resolve_category_from_label("Прочее", {})
    assert code is None

    assert not looks_like_catalog_code("T6")
    assert looks_like_catalog_code("521-101-0420")


def test_apply_catalog_categories():
    products = [
        SimpleNamespace(
            code="521-201-0500",
            name='Предмет "Музыка"',
            category_code=None,
            category_name=None,
        ),
        SimpleNamespace(
            code="521-201-0501",
            name="Пианино",
            category_code=None,
            category_name=None,
        ),
    ]
    updated = apply_catalog_categories(products)
    assert updated == 2
    assert products[1].category_code == "521-201-0500"
    assert products[1].category_name == 'Предмет "Музыка"'
