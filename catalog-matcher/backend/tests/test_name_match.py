from app.matching.name_match import (
    anchor_overlap,
    adjust_candidate_score,
    significant_tokens,
)


def test_significant_tokens_skip_rubber_adjective():
    tokens = significant_tokens("Грелка резиновая")
    assert "грелка" in tokens
    assert "резиновая" not in tokens


def test_no_anchor_overlap_rubber_ball_vs_hot_water_bottle():
    assert not anchor_overlap("Грелка резиновая", "Мяч резиновый")
    assert not anchor_overlap("Жгут резиновый", "Мяч резиновый")


def test_anchor_overlap_syringe_not_board():
    assert not anchor_overlap(
        "Шприцы одноразовые с иглами 5,0",
        "Доска наклонная навесная 1,5м",
    )


def test_anchor_overlap_furniture_subtype():
    # «мебель» and «стул» do not share a literal token — category handles that case.
    assert not anchor_overlap("Мебель для ИЗО и Ателье", "Стул крутящийся без спинки")


def test_adjust_score_caps_embedding_false_positive():
    score, note = adjust_candidate_score(
        "Жгут резиновый",
        "Мяч резиновый",
        retriever_score=0.82,
    )
    assert score < 0.45
    assert "no keyword" in note or "weak" in note
