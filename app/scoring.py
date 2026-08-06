from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Mapping

from .rubric import DIMENSION_ORDER, RUBRICS


TWO_PLACES = Decimal("0.01")
FIVE = Decimal("5")


class ScoringError(ValueError):
    pass


def _decimal(value: object, label: str) -> Decimal:
    try:
        number = Decimal(str(value).strip())
    except Exception as exc:
        raise ScoringError(f"{label} must be a valid number.") from exc
    if not number.is_finite():
        raise ScoringError(f"{label} must be a finite number.")
    return number


def validate_grade(value: object, label: str = "Grade") -> Decimal:
    grade = _decimal(value, label)
    if grade < 0 or grade > 5:
        raise ScoringError(f"{label} must be between 0 and 5.")
    if grade * 10 != (grade * 10).to_integral_value():
        raise ScoringError(f"{label} must use increments of 0.1.")
    return grade


def validate_general_grade(value: object, label: str) -> Decimal:
    grade = _decimal(value, label)
    if grade < 0 or grade > 1:
        raise ScoringError(f"{label} must be between 0 and 1.")
    if grade * 10 != (grade * 10).to_integral_value():
        raise ScoringError(f"{label} must use increments of 0.1.")
    return grade


def weighted_activity_score(activity_code: str, responses: Mapping[str, object]) -> tuple[Decimal, dict[str, Decimal]]:
    rubric = RUBRICS.get(activity_code)
    if not rubric or rubric.kind != "weighted":
        raise ScoringError("Unknown weighted activity.")
    expected = {criterion.key for criterion in rubric.criteria}
    provided = set(responses)
    if provided != expected:
        missing = sorted(expected - provided)
        extra = sorted(provided - expected)
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if extra:
            details.append("unexpected: " + ", ".join(extra))
        raise ScoringError("Every rubric criterion is required (" + "; ".join(details) + ").")
    normalized: dict[str, Decimal] = {}
    score = Decimal("0")
    total_weight = sum((criterion.weight for criterion in rubric.criteria), Decimal("0"))
    if total_weight <= 0:
        raise ScoringError("The activity rubric has no positive weight.")
    for criterion in rubric.criteria:
        grade = validate_grade(responses[criterion.key], criterion.name)
        normalized[criterion.key] = grade
        score += grade * criterion.weight
    official = (score / total_weight).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    return official, normalized


def parse_duration(value: object) -> Decimal:
    raw = str(value).strip().lower()
    if not raw:
        raise ScoringError("Duration is required.")

    if re.fullmatch(r"\d+(?:\.\d+)?", raw):
        seconds = Decimal(raw)
        if seconds < 0:
            raise ScoringError("Duration cannot be negative.")
        return seconds

    if ":" in raw:
        if not re.fullmatch(r"\d+(?::\d{1,2}){1,2}", raw):
            raise ScoringError("Use MM:SS or HH:MM:SS for colon-formatted durations.")
        parts = [int(part) for part in raw.split(":")]
        if parts[-1] >= 60 or (len(parts) == 3 and parts[-2] >= 60):
            raise ScoringError("Seconds and HH:MM:SS minutes must be below 60.")
        if len(parts) == 2:
            return Decimal(parts[0] * 60 + parts[1])
        return Decimal(parts[0] * 3600 + parts[1] * 60 + parts[2])

    compact = re.sub(r"\s+", "", raw)
    compact = compact.replace("minutes", "m").replace("minute", "m")
    compact = compact.replace("mins", "m").replace("min", "m")
    compact = compact.replace("seconds", "s").replace("second", "s")
    compact = compact.replace("secs", "s").replace("sec", "s")
    match = re.fullmatch(
        r"(?:(?P<h>\d+(?:\.\d+)?)h)?(?:(?P<m>\d+(?:\.\d+)?)m)?(?:(?P<s>\d+(?:\.\d+)?)(?:s)?)?",
        compact,
    )
    if not match or not any(match.groupdict().values()) or compact[-1].isdigit() and "m" not in compact and "h" not in compact:
        raise ScoringError("Use seconds, MM:SS, HH:MM:SS, or units such as 1m30s.")
    hours = Decimal(match.group("h") or "0")
    minutes = Decimal(match.group("m") or "0")
    seconds = Decimal(match.group("s") or "0")
    total = hours * 3600 + minutes * 60 + seconds
    if total < 0:
        raise ScoringError("Duration cannot be negative.")
    return total


def sport_score(payload: Mapping[str, object]) -> tuple[Decimal, dict[str, Decimal], dict[str, Decimal]]:
    required = {"push_ups", "wall_sit", "beep_test", "plank"}
    if not required.issubset(payload):
        raise ScoringError("Push-ups, wall sit, beep test, and plank are required.")
    push_ups = _decimal(payload["push_ups"], "Push-ups")
    if push_ups < 0 or push_ups != push_ups.to_integral_value():
        raise ScoringError("Push-ups must be a non-negative whole number.")
    normalized_values = {
        "push_ups": push_ups,
        "wall_sit": parse_duration(payload["wall_sit"]),
        "beep_test": parse_duration(payload["beep_test"]),
        "plank": parse_duration(payload["plank"]),
    }
    exercise_scores: dict[str, Decimal] = {}
    for criterion in RUBRICS["sport"].criteria:
        result = normalized_values[criterion.key]
        exercise_scores[criterion.key] = min(result / criterion.target * FIVE, FIVE)
    official = (sum(exercise_scores.values(), Decimal("0")) / Decimal("4")).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    return official, normalized_values, exercise_scores


def general_average(values: Mapping[str, object | None]) -> Decimal:
    total = Decimal("0")
    for key, label in (("punctuality", "Punctuality"), ("respect", "Respect"), ("seriousness", "Seriousness")):
        value = values.get(key)
        total += Decimal("0") if value is None else validate_general_grade(value, label)
    return total / Decimal("3")


def overall_score(dimension_scores: Mapping[str, object], general_score: object) -> Decimal:
    dimension_total = sum(
        (_decimal(dimension_scores.get(code, 0), code) for code in DIMENSION_ORDER),
        Decimal("0"),
    )
    general = _decimal(general_score, "General average")
    return Decimal("20") / Decimal("7") * (dimension_total + general)


def color_grade(score: object) -> str:
    value = _decimal(score, "Overall score")
    if value < 13:
        return "red"
    if value < 16:
        return "yellow"
    return "green"


def competition_ranks(values: list[tuple[str, Decimal]]) -> dict[str, int]:
    sorted_values = sorted(values, key=lambda item: (-item[1], item[0]))
    ranks: dict[str, int] = {}
    previous: Decimal | None = None
    current_rank = 0
    for position, (identifier, score) in enumerate(sorted_values, start=1):
        if previous is None or score != previous:
            current_rank = position
            previous = score
        ranks[identifier] = current_rank
    return ranks
