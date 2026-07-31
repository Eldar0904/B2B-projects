"""
Test cases for app.services.normalize.

Run with:  pytest tests/test_normalize.py -v
(or, from backend/: python -m pytest tests/test_normalize.py -v)

These are pure unit tests — normalize.py has no DB/network dependencies,
so no fixtures or app startup are needed.
"""
import pytest

from app.services.normalize import (
    clean_text, clean_code, to_float, build_normalized_text,
)


class TestCleanText:
    def test_lowercases_and_strips_punctuation(self):
        assert clean_text("Steel Pipe, 20mm!") == "steel pipe 20mm"

    def test_collapses_whitespace(self):
        assert clean_text("  Extra   Spaces   Here ") == "extra spaces here"

    def test_handles_none(self):
        assert clean_text(None) == ""

    def test_handles_empty_string(self):
        assert clean_text("") == ""

    def test_treats_nan_like_values_as_empty(self):
        for v in ["nan", "NaN", "None", "N/A", "n/a", "-"]:
            assert clean_text(v) == ""

    def test_preserves_russian_text(self):
        assert clean_text("Труба стальная, 20мм") == "труба стальная 20мм"

    def test_mixed_ru_en_text(self):
        result = clean_text("Кабель ВВГ (Cable) 3x2.5")
        # digits/punctuation like the dot get stripped, letters kept, spaces collapsed
        assert result == "кабель ввг cable 3x2 5"

    def test_unit_normalization_mm(self):
        assert "mm" in clean_text("Pipe 20 mm diameter")

    def test_case_insensitive_unit_normalization(self):
        # unit regex runs on already-lowercased text, so this mainly checks
        # that MM doesn't survive as uppercase and isn't mangled
        result = clean_text("Pipe 20 MM diameter")
        assert "mm" in result
        assert "MM" not in result

    def test_accented_unicode_is_normalized(self):
        # NFKC normalization should not crash and should lowercase consistently
        result = clean_text("Café Ürün")
        assert result != ""
        assert result == result.lower()


class TestCleanCode:
    def test_uppercases_and_strips(self):
        assert clean_code("  abc-123  ") == "ABC-123"

    def test_handles_none(self):
        assert clean_code(None) == ""

    def test_treats_nan_like_values_as_empty(self):
        for v in ["nan", "NaN", "None", ""]:
            assert clean_code(v) == ""

    def test_numeric_float_from_excel(self):
        assert clean_code(12345.0) == "12345"

    def test_nan_float_from_excel(self):
        assert clean_code(float("nan")) == ""

    def test_numeric_int_preserved(self):
        assert clean_code(42) == "42"


class TestToFloat:
    def test_parses_plain_number(self):
        assert to_float("123.45") == 123.45

    def test_parses_number_with_thousands_comma(self):
        assert to_float("12,345.67") == 12345.67

    def test_parses_number_with_spaces(self):
        assert to_float("12 345") == 12345.0

    def test_returns_none_for_none(self):
        assert to_float(None) is None

    def test_returns_none_for_empty_string(self):
        assert to_float("") is None

    def test_returns_none_for_non_numeric(self):
        assert to_float("not a number") is None

    def test_returns_none_for_nan_float(self):
        assert to_float(float("nan")) is None

    def test_parses_int_input(self):
        assert to_float(42) == 42.0


class TestBuildNormalizedText:
    def test_concatenates_multiple_fields(self):
        result = build_normalized_text("Steel Pipe", "Acme Corp", "Model X", "20mm diameter")
        assert result == "steel pipe acme corp model x 20mm diameter"

    def test_skips_none_and_empty_parts(self):
        result = build_normalized_text("Steel Pipe", None, "", "  ")
        assert result == "steel pipe"

    def test_all_empty_parts_returns_empty_string(self):
        assert build_normalized_text(None, "", "nan") == ""

    def test_russian_fields(self):
        result = build_normalized_text("Труба стальная", "20мм", None)
        assert result == "труба стальная 20мм"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
