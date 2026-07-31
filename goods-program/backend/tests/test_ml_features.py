from app.services.ml.features import compute_price_difference


def test_price_difference_normal_case():
    diff, available = compute_price_difference(100.0, 120.0)
    assert available == 1.0
    assert round(diff, 4) == round(20 / 120, 4)


def test_price_difference_identical_prices():
    diff, available = compute_price_difference(100.0, 100.0)
    assert available == 1.0
    assert diff == 0.0


def test_price_difference_missing_price_defaults_to_max_with_unavailable_flag():
    diff, available = compute_price_difference(None, 100.0)
    assert available == 0.0
    assert diff == 1.0

    diff2, available2 = compute_price_difference(100.0, None)
    assert available2 == 0.0
    assert diff2 == 1.0


def test_price_difference_zero_or_negative_price_treated_as_unavailable():
    diff, available = compute_price_difference(0.0, 100.0)
    assert available == 0.0
    assert diff == 1.0
