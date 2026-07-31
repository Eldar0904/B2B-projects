"""Tests for the v2 normalizer: punctuation, Kazakh, and dimensions.

The previous normalizer's docstring claimed it stripped "punctuation
noise" but the implementation only collapsed whitespace, and its token
class ([a-zа-я0-9]) silently split every Kazakh word containing ә ғ қ ң ө
ұ ү һ і - 12.4% of the rows in the real destination file.
"""

from app.services.normalizer import build_normalized_name, normalize_text, tokenize


class TestPunctuation:
    def test_guillemets_and_parentheses_are_removed(self):
        assert normalize_text("«3Д ұшбұрыш» (Интеллектум)") == "3д ұшбұрыш интеллектум"

    def test_commas_are_removed(self):
        assert normalize_text("Игровой набор «Полиция», 12 предметов") == (
            "игровой набор полиция 12 предметов"
        )

    def test_decimal_points_inside_numbers_survive(self):
        """Stripping every '.' and ',' would corrupt measurements."""
        assert "1,5" in normalize_text("Труба 1,5 метра")
        assert "0.4" in normalize_text("кромка ПВХ 0.4 мм")

    def test_trailing_sentence_punctuation_is_removed(self):
        assert normalize_text("Ертегілер. Өзіміз оқимыз") == "ертегілер өзіміз оқимыз"


class TestKazakh:
    def test_kazakh_letters_survive_normalization(self):
        assert normalize_text("Динозаврлар энциклопедия") == "динозаврлар энциклопедия"
        assert normalize_text("«Пішіндер»") == "пішіндер"

    def test_kazakh_words_tokenize_as_whole_words(self):
        """The old [a-zа-я0-9] class split these apart at every Kazakh letter."""
        assert tokenize("ұшбұрыш пішіндер") == ["ұшбұрыш", "пішіндер"]

    def test_kazakh_stopwords_are_dropped(self):
        assert "және" not in tokenize("үстел және орындық")

    def test_kazakh_yo_rule_does_not_touch_distinct_letters(self):
        """ё->е must not corrupt Kazakh ө, which is a different letter."""
        assert "ө" in normalize_text("Өзіміз")


class TestDimensions:
    def test_asterisk_and_cyrillic_x_unify(self):
        latin = normalize_text("Стол 900x900x570")
        cyrillic = normalize_text("Стол 900х900х570")
        asterisk = normalize_text("Стол 900*900*570")
        assert latin == cyrillic == asterisk

    def test_spaced_dimensions_unify(self):
        assert normalize_text("900 x 600") == normalize_text("900x600")

    def test_dimension_stays_one_token(self):
        assert tokenize("стол 900x600") == ["стол", "900x600"]


class TestGeneral:
    def test_case_and_whitespace_are_normalized(self):
        assert normalize_text("  СТОЛ   Детский ") == "стол детский"

    def test_yo_is_folded(self):
        assert normalize_text("ёлка") == normalize_text("елка")

    def test_none_and_empty_are_safe(self):
        assert normalize_text(None) == ""
        assert normalize_text("") == ""
        assert tokenize("") == []

    def test_stopwords_removed_by_default_but_optional(self):
        assert tokenize("стол для детей") == ["стол", "детей"]
        assert "для" in tokenize("стол для детей", drop_stopwords=False)

    def test_build_normalized_name_ignores_description(self):
        """Folding descriptions in was measured to drop top-1 accuracy from
        94.1% to 61.8% on the real files - the two sides' descriptions are
        not comparable.
        """
        assert build_normalized_name("Грелка резиновая", "длинное маркетинговое описание") == (
            "грелка резиновая"
        )
