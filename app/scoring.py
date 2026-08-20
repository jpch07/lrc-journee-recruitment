from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Mapping

from .assessment_runtime import active_assessment_definition, activity_definition
from .rubric import RUBRICS


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


def validate_rating(activity_code: str, criterion_key: str, value: object) -> Decimal:
    activity = activity_definition(activity_code)
    criterion = next((item for item in activity.criteria if item.key == criterion_key), None) if activity else None
    if not criterion:
        raise ScoringError("Unknown evaluation criterion.")
    grade = _decimal(value, criterion.name)
    minimum, maximum, step = Decimal(criterion.minimum), Decimal(criterion.maximum), Decimal(criterion.step)
    if grade < minimum or grade > maximum:
        raise ScoringError(f"{criterion.name} must be between {minimum} and {maximum}.")
    if (grade - minimum) / step != ((grade - minimum) / step).to_integral_value():
        raise ScoringError(f"{criterion.name} must use increments of {step}.")
    return grade


def validate_general_grade(value: object, label: str) -> Decimal:
    grade = _decimal(value, label)
    if grade < 0 or grade > 1:
        raise ScoringError(f"{label} must be between 0 and 1.")
    if grade * 10 != (grade * 10).to_integral_value():
        raise ScoringError(f"{label} must use increments of 0.1.")
    return grade


def validate_general_factor(storage_key: str, value: object) -> Decimal:
    factor = next((item for item in active_assessment_definition().generalFactors
                   if item.storageKey == storage_key), None)
    if factor is None:
        raise ScoringError("This general assessment factor is disabled.")
    grade = _decimal(value, factor.name)
    maximum, step = Decimal(factor.maximum), Decimal(factor.step)
    if grade < 0 or grade > maximum:
        raise ScoringError(f"{factor.name} must be between 0 and {maximum}.")
    if grade / step != (grade / step).to_integral_value():
        raise ScoringError(f"{factor.name} must use increments of {step}.")
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
        grade = validate_rating(activity_code, criterion.key, responses[criterion.key])
        normalized[criterion.key] = grade
        configured = next(item for item in activity_definition(activity_code).criteria if item.key == criterion.key)
        scale = Decimal(configured.maximum) - Decimal(configured.minimum)
        score += ((grade - Decimal(configured.minimum)) / scale * FIVE) * criterion.weight
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


def target_activity_score(
    activity_code: str, payload: Mapping[str, object]
) -> tuple[Decimal, dict[str, Decimal], dict[str, Decimal]]:
    activity = activity_definition(activity_code)
    if not activity or activity.scoring != "target_average":
        raise ScoringError("Unknown target-based activity.")
    required = {item.key for item in activity.criteria}
    provided = set(payload)
    if provided != required:
        missing, extra = sorted(required - provided), sorted(provided - required)
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if extra:
            details.append("unexpected: " + ", ".join(extra))
        raise ScoringError("Every activity result is required (" + "; ".join(details) + ").")
    normalized_values: dict[str, Decimal] = {}
    for criterion in activity.criteria:
        if criterion.inputType == "duration":
            value = parse_duration(payload[criterion.key])
        else:
            value = _decimal(payload[criterion.key], criterion.name)
            if value < 0:
                raise ScoringError(f"{criterion.name} cannot be negative.")
            if criterion.inputType == "integer" and value != value.to_integral_value():
                raise ScoringError(f"{criterion.name} must be a whole number.")
        normalized_values[criterion.key] = value
    exercise_scores: dict[str, Decimal] = {}
    weighted_total = Decimal("0")
    total_weight = Decimal("0")
    for criterion in activity.criteria:
        result = normalized_values[criterion.key]
        target = Decimal(criterion.target)
        if criterion.direction == "higher":
            converted = min(result / target * FIVE, FIVE)
        elif result == 0:
            converted = FIVE
        else:
            converted = min(target / result * FIVE, FIVE)
        exercise_scores[criterion.key] = converted
        weighted_total += converted * Decimal(criterion.weight)
        total_weight += Decimal(criterion.weight)
    official = (weighted_total / total_weight).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    return official, normalized_values, exercise_scores


def sport_score(payload: Mapping[str, object]) -> tuple[Decimal, dict[str, Decimal], dict[str, Decimal]]:
    """Compatibility wrapper retained for imports and the historical LRC tests."""
    return target_activity_score("sport", payload)


def general_average(values: Mapping[str, object | None]) -> Decimal:
    definition = active_assessment_definition()
    if not definition.generalFactors:
        return Decimal("0")
    total = Decimal("0")
    for factor in definition.generalFactors:
        value = values.get(factor.storageKey)
        if value is None:
            continue
        grade = _decimal(value, factor.name)
        maximum, step = Decimal(factor.maximum), Decimal(factor.step)
        if grade < 0 or grade > maximum or grade / step != (grade / step).to_integral_value():
            raise ScoringError(f"{factor.name} must be between 0 and {maximum} in increments of {step}.")
        total += grade / maximum
    return total / Decimal(len(definition.generalFactors))


def overall_score(
    dimension_scores: Mapping[str, object], general_score: object,
    complete_components: set[tuple[str, str]] | None = None,
) -> Decimal:
    definition = active_assessment_definition()
    weighted = Decimal("0")
    included_weight = Decimal("0")
    for component in definition.scoring.components:
        marker = (component.source, component.key)
        if (definition.scoring.missingComponents == "exclude" and complete_components is not None
                and marker not in complete_components):
            continue
        value = (_decimal(general_score, "General average") if component.source == "general"
                 else _decimal(dimension_scores.get(component.key, 0), component.key))
        weighted += value * Decimal(component.weight)
        included_weight += Decimal(component.weight)
    if included_weight == 0:
        return Decimal("0")
    normalized = weighted / included_weight if definition.scoring.missingComponents == "exclude" else weighted
    return normalized * Decimal(definition.scoring.officialMaximum)


def color_grade(score: object) -> str:
    value = _decimal(score, "Overall score")
    chosen = sorted(active_assessment_definition().scoring.bands, key=lambda item: item.minimum)[0]
    for band in sorted(active_assessment_definition().scoring.bands, key=lambda item: item.minimum):
        if value >= Decimal(band.minimum):
            chosen = band
    return chosen.key


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


def configured_ranks(values: list[tuple[str, Decimal]]) -> dict[str, int]:
    mode = active_assessment_definition().scoring.ranking
    sorted_values = sorted(values, key=lambda item: (-item[1], item[0]))
    if mode == "competition":
        return competition_ranks(values)
    ranks: dict[str, int] = {}
    previous: Decimal | None = None
    dense_rank = 0
    for position, (identifier, score) in enumerate(sorted_values, start=1):
        if mode == "ordinal":
            ranks[identifier] = position
        else:
            if previous is None or score != previous:
                dense_rank += 1
                previous = score
            ranks[identifier] = dense_rank
    return ranks
