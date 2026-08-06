from __future__ import annotations

from decimal import Decimal

import pytest

from app.rubric import (
    BEHAVIORAL_DIMENSIONS,
    DIMENSION_ACTIVITIES,
    DIMENSION_ORDER,
    RUBRICS,
    validate_rubrics,
)
from app.scoring import (
    ScoringError,
    color_grade,
    competition_ranks,
    overall_score,
    parse_duration,
    sport_score,
    validate_general_grade,
    validate_grade,
    weighted_activity_score,
)


def test_rubric_weights_are_exact():
    validate_rubrics()
    for code in ("sport", "skills"):
        assert sum((criterion.weight for criterion in RUBRICS[code].criteria), Decimal("0")) == Decimal("1.00")
    for dimension in BEHAVIORAL_DIMENSIONS:
        total = sum(
            criterion.weight
            for code in DIMENSION_ACTIVITIES
            for criterion in RUBRICS[code].criteria
            if criterion.dimension.casefold() == dimension
        )
        assert abs(total - Decimal("1")) < Decimal("1e-24")


def test_grade_ranges_and_steps():
    assert validate_grade("4.9") == Decimal("4.9")
    assert validate_general_grade("0.7", "Respect") == Decimal("0.7")
    for invalid in (-0.1, 5.1, 1.25):
        with pytest.raises(ScoringError):
            validate_grade(invalid)
    for invalid in (-0.1, 1.1, 0.15):
        with pytest.raises(ScoringError):
            validate_general_grade(invalid, "General")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("90", 90), ("01:30", 90), ("1:02:03", 3723), ("1m30s", 90), ("1m30", 90), ("1m 30s", 90), ("2 minutes", 120)],
)
def test_duration_formats(raw, expected):
    assert parse_duration(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", ["1:99", "abc", "-4", "1::30"])
def test_duration_rejects_ambiguous_or_invalid(raw):
    with pytest.raises(ScoringError):
        parse_duration(raw)


def test_sport_targets_cap_and_round():
    score, values, exercises = sport_score(
        {"push_ups": 30, "wall_sit": "120s", "beep_test": "10:00", "plank": "2m"}
    )
    assert score == Decimal("5.00")
    assert all(value == Decimal("5") for value in exercises.values())
    capped, *_ = sport_score(
        {"push_ups": 300, "wall_sit": "1200", "beep_test": "6000", "plank": "1200"}
    )
    assert capped == Decimal("5.00")
    assert values["beep_test"] == Decimal("600")


def test_weighted_score_and_required_criteria():
    responses = {criterion.key: Decimal("5") for criterion in RUBRICS["escape_room"].criteria}
    score, _ = weighted_activity_score("escape_room", responses)
    assert score == Decimal("5.00")
    responses.pop(next(iter(responses)))
    with pytest.raises(ScoringError):
        weighted_activity_score("escape_room", responses)


def test_overall_maximum_colors_and_competition_rank():
    assert overall_score({code: 1 for code in DIMENSION_ORDER}, 1) == Decimal("20")
    assert overall_score({code: Decimal("0.5") for code in DIMENSION_ORDER}, Decimal("0.5")) == Decimal("10.0")
    assert color_grade(Decimal("12.999")) == "red"
    assert color_grade(Decimal("13")) == "yellow"
    assert color_grade(Decimal("15.999")) == "yellow"
    assert color_grade(Decimal("16")) == "green"
    assert competition_ranks([("a", Decimal("20")), ("b", Decimal("18")), ("c", Decimal("18")), ("d", Decimal("15"))]) == {"a": 1, "b": 2, "c": 2, "d": 4}
