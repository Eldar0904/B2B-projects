from app.services.normalizer import build_normalized_name, normalize_text


def test_case_and_whitespace_normalize_the_same():
    assert normalize_text("Стол детский") == normalize_text("СТОЛ ДЕТСКИЙ")
    assert normalize_text("Стол детский") == normalize_text("стол   детский")


def test_yo_and_ye_normalize_the_same():
    assert normalize_text("тёплый") == normalize_text("теплый")


def test_quotes_and_hyphens_normalize():
    assert normalize_text('«Ультрадар»') == normalize_text('"Ультрадар"')
    assert normalize_text("2020–2021") == normalize_text("2020-2021")


def test_none_returns_empty_string():
    assert normalize_text(None) == ""


def test_build_normalized_name_uses_product_name_only():
    assert build_normalized_name("Стол детский", "some description") == "стол детский"
