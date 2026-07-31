"""Tests for app/services/attributes.py (HANDOFF.md section 5, Task 2).

Real examples are taken directly from HANDOFF.md's own worked cases, so a
regression here means the parser stopped handling the exact data the task
was written to solve.
"""

from app.services.attributes import (
    extract_attributes,
    extract_dimensions,
    extract_material,
    normalize_unit,
)


def test_extract_dimensions_two_numbers_mm():
    dims = extract_dimensions("Манеж детский размерами 830х680 мм")
    assert dims.width_mm == 830.0
    assert dims.height_mm == 680.0
    assert dims.depth_mm is None


def test_extract_dimensions_three_numbers_mm():
    dims = extract_dimensions("Манеж детский размерами 840х840х680 мм")
    assert dims.width_mm == 840.0
    assert dims.height_mm == 840.0
    assert dims.depth_mm == 680.0


def test_extract_dimensions_handles_every_separator_variant():
    """normalizer.py unifies *, х (Cyrillic), x (Latin), × into one form
    before this module ever sees the text - this locks in that all three
    variants from the real catalog parse identically.
    """
    star = extract_dimensions("Корзина 420*840")
    cyrillic_x = extract_dimensions("Корзина 420х840")
    latin_x = extract_dimensions("Корзина 420x840")
    mult_sign = extract_dimensions("Корзина 420×840")

    for dims in (star, cyrillic_x, latin_x, mult_sign):
        assert dims.width_mm == 420.0
        assert dims.height_mm == 840.0


def test_extract_dimensions_converts_centimeters_to_millimeters():
    dims = extract_dimensions("Стол детский 120х60 см")
    assert dims.width_mm == 1200.0
    assert dims.height_mm == 600.0


def test_extract_dimensions_none_when_no_pattern_present():
    dims = extract_dimensions("Совершенно другой товар xyz")
    assert dims == extract_dimensions(None)  # both "no dimensions found"
    assert dims.width_mm is None
    assert dims.height_mm is None
    assert dims.depth_mm is None


def test_extract_dimensions_prefers_dedicated_column_over_free_text():
    """When a sheet has a real "Размеры" column (column_mapper.py's
    "dimensions" canonical field), that should win over parsing the name,
    which may contain an unrelated number (a code, a quantity...).
    """
    dims = extract_dimensions("900х450", "Стол артикул 100х200 модель")
    assert dims.width_mm == 900.0
    assert dims.height_mm == 450.0


def test_extract_material_recognizes_common_stems():
    assert extract_material("Шкаф стеллаж, ЛДСП") == "лдсп"
    assert extract_material("Стол деревянный") == "дерево"
    assert extract_material("Ковер текстильный") == "текстиль"
    assert extract_material("Мяч резиновый") == "резина"


def test_extract_material_prefers_specific_compound_over_generic_substring():
    """"лдсп" contains "дсп" as a substring, and "нержавеющая сталь"
    contains "сталь" - the more specific term must win in both cases, or
    every ЛДСП product would be misreported as plain ДСП.
    """
    assert extract_material("Тумба из ЛДСП") == "лдсп"
    assert extract_material("Стеллаж, нержавеющая сталь") == "нержавеющая сталь"


def test_extract_material_none_when_nothing_recognized():
    assert extract_material("Обучающие плакаты для дошкольников") is None
    assert extract_material(None) is None


def test_normalize_unit_collapses_common_spellings():
    assert normalize_unit("шт.") == "шт"
    assert normalize_unit("штук") == "шт"
    assert normalize_unit("шт") == "шт"
    assert normalize_unit("компл.") == "компл"


def test_normalize_unit_passes_through_unrecognized_unit():
    assert normalize_unit("бухта") == "бухта"


def test_normalize_unit_none_for_missing_value():
    assert normalize_unit(None) is None
    assert normalize_unit("") is None


def test_extract_attributes_wraps_all_three_extractors():
    result = extract_attributes(
        "Манеж детский размерами 830х680 мм, ЛДСП",
        description=None,
        raw_unit="шт.",
    )
    assert result.dim_w_mm == 830.0
    assert result.dim_h_mm == 680.0
    assert result.dim_d_mm is None
    assert result.material == "лдсп"
    assert result.unit_normalized == "шт"


def test_extract_attributes_handles_all_none_inputs():
    result = extract_attributes(None)
    assert result.dim_w_mm is None
    assert result.material is None
    assert result.unit_normalized is None
