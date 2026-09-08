from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping

from .models import GeneralAssessment
from .utils import dumps, loads


LEGACY_GENERAL_FACTOR_KEYS = ("punctuality", "respect", "seriousness")


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def stored_general_assessment_values(
    assessment: GeneralAssessment | None,
) -> dict[str, Decimal | None]:
    """Return every stored factor without interpreting keys as model attributes.

    The three original columns remain a compatibility fallback for databases and
    records created before dynamic factors were introduced.  A key explicitly
    present in ``values_json`` takes precedence, including an explicit null.
    """
    if assessment is None:
        return {}
    raw = loads(assessment.values_json, {})
    values = {
        str(key): _decimal_or_none(value)
        for key, value in raw.items()
    } if isinstance(raw, dict) else {}
    for key in LEGACY_GENERAL_FACTOR_KEYS:
        if key not in values:
            values[key] = _decimal_or_none(getattr(assessment, key))
    return values


def configured_general_assessment_values(
    assessment: GeneralAssessment | None,
    factor_keys: Iterable[str],
) -> dict[str, Decimal | None]:
    stored = stored_general_assessment_values(assessment)
    return {key: stored.get(key) for key in factor_keys}


def general_assessment_values_payload(
    assessment: GeneralAssessment | None,
    factor_keys: Iterable[str],
) -> dict[str, float | None]:
    return {
        key: float(value) if value is not None else None
        for key, value in configured_general_assessment_values(assessment, factor_keys).items()
    }


def set_general_assessment_values(
    assessment: GeneralAssessment,
    values: Mapping[str, Decimal | None],
) -> None:
    """Persist a complete value map and mirror the original LRC columns."""
    normalized = {
        str(key): _decimal_or_none(value)
        for key, value in values.items()
    }
    assessment.values_json = dumps(dict(sorted(normalized.items())))
    for key in LEGACY_GENERAL_FACTOR_KEYS:
        value = normalized.get(key)
        # These compatibility columns are NUMERIC(3, 2), while a configurable
        # factor may use a scale as large as 100. JSON is authoritative for the
        # generic system, so never overflow an old LRC-only mirror column.
        setattr(
            assessment,
            key,
            value if value is None or Decimal("-9.99") <= value <= Decimal("9.99") else None,
        )
