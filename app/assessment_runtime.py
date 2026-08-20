from __future__ import annotations

from decimal import Decimal
from contextvars import ContextVar

from .assessment_config import AssessmentSystemDefinition, lrc_assessment_definition
from .rubric import (
    ACTIVITY_ORDER,
    BEHAVIORAL_DIMENSIONS,
    DIMENSION_ACTIVITIES,
    DIMENSION_NAMES,
    DIMENSION_ORDER,
    ROOM_ACTIVITIES,
    RUBRICS,
    ActivityRubric,
    Criterion,
    reset_runtime_data,
    set_runtime_data,
)


_active_definition: ContextVar[AssessmentSystemDefinition] = ContextVar(
    "active_assessment_definition", default=lrc_assessment_definition()
)


def active_assessment_definition() -> AssessmentSystemDefinition:
    return _active_definition.get()


def activate_assessment_definition(definition: AssessmentSystemDefinition):
    """Apply the single published system definition to the proven LRC runtime.

    The legacy modules import these collections once, so they are deliberately
    mutated in place instead of replaced. One deployment has one active assessment
    system, keeping operations predictable and the configurator small.
    """
    definition_token = _active_definition.set(definition)
    activities = [item for item in definition.activities if item.enabled]
    dimension_activities = [
        activity.key for activity in activities
        if any(criterion.dimensionKey for criterion in activity.criteria)
    ]
    rubrics = {}
    for activity in activities:
        rubrics[activity.key] = ActivityRubric(
            code=activity.key,
            name=activity.name,
            kind="sport" if activity.scoring == "target_average" else "weighted",
            criteria=tuple(Criterion(
                key=item.key,
                dimension=DIMENSION_NAMES.get(item.dimensionKey, item.dimensionName),
                name=item.name,
                explanation=item.explanation,
                weight=Decimal(item.weight),
                input_type={"rating": "grade", "integer": "integer", "duration": "duration", "number": "number"}[item.inputType],
                target=Decimal(item.target) if item.target is not None else None,
                unit=item.unit,
            ) for item in activity.criteria),
        )
    rubric_token = set_runtime_data({
        "activity_order": [item.key for item in activities],
        "room_activities": {item.key for item in activities if item.assignment.mode == "automatic_groups"},
        "behavioral_dimensions": [item.key for item in definition.dimensions if item.source == "criteria"],
        "dimension_order": [item.key for item in definition.dimensions],
        "dimension_names": {item.key: item.name for item in definition.dimensions},
        "dimension_activities": dimension_activities,
        "rubrics": rubrics,
    })
    return definition_token, rubric_token


def reset_assessment_definition(tokens) -> None:
    if not tokens:
        return
    definition_token, rubric_token = tokens
    reset_runtime_data(rubric_token)
    _active_definition.reset(definition_token)


def activity_definition(key: str):
    return next((item for item in active_assessment_definition().activities if item.enabled and item.key == key), None)


def primary_category_order() -> list[str]:
    return [item.key for item in sorted(
        active_assessment_definition().assessors.categories,
        key=lambda item: (item.primaryPriority, item.name.casefold()),
    )]


def secondary_category_order() -> list[str]:
    return [item.key for item in sorted(
        active_assessment_definition().assessors.categories,
        key=lambda item: (item.secondaryPriority, item.name.casefold()),
    )]


def assessor_category_keys() -> set[str]:
    return {item.key for item in active_assessment_definition().assessors.categories}


def default_assessor_category() -> str:
    ordered = secondary_category_order()
    return ordered[0] if ordered else primary_category_order()[0]
