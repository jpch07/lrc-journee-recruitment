from __future__ import annotations

import re
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .assessment_config import (
    AssessmentSystemDefinition,
    blank_assessment_definition,
    lrc_assessment_definition,
)
from .assessment_runtime import activate_assessment_definition
from .models import (
    AssessmentSystem,
    AssessmentSystemVersion,
    Evaluator,
    EvaluatorDirectory,
    Journey,
    UserAccount,
)
from .utils import dumps, loads
from .tenant import current_system_id


def _definition_json(definition: AssessmentSystemDefinition) -> str:
    return dumps(definition.model_dump(mode="json"))


def system_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return (slug or "assessment")[:90]


def ensure_assessment_system(db: Session) -> AssessmentSystem:
    selected_id = current_system_id()
    query = select(AssessmentSystem)
    if selected_id:
        query = query.where(AssessmentSystem.id == selected_id)
    system = db.scalar(query.order_by(AssessmentSystem.created_at).limit(1))
    if not system:
        definition = lrc_assessment_definition()
        system = AssessmentSystem(
            name=definition.name,
            slug=system_slug(definition.name),
            draft_json=_definition_json(definition),
            published_version=1,
            updated_by="System bootstrap",
        )
        db.add(system)
        db.flush()
        db.add(AssessmentSystemVersion(
            system_id=system.id,
            version=1,
            definition_json=_definition_json(definition),
            change_summary="Finalized LRC system compatibility preset.",
            published_by="System bootstrap",
        ))
        db.flush()
    definition = published_definition(db, system)
    activate_assessment_definition(definition)
    return system


def published_record(db: Session, system: AssessmentSystem) -> AssessmentSystemVersion:
    record = db.scalar(select(AssessmentSystemVersion).where(
        AssessmentSystemVersion.system_id == system.id,
        AssessmentSystemVersion.version == system.published_version,
    ))
    if not record:
        raise RuntimeError("The published assessment-system definition is missing.")
    return record


def published_definition(db: Session, system: AssessmentSystem) -> AssessmentSystemDefinition:
    return AssessmentSystemDefinition.model_validate(loads(published_record(db, system).definition_json, {}))


def draft_definition(system: AssessmentSystem) -> AssessmentSystemDefinition:
    return AssessmentSystemDefinition.model_validate(loads(system.draft_json, {}))


def preset_definition(key: str) -> AssessmentSystemDefinition:
    if key == "lrc_2026":
        return lrc_assessment_definition()
    if key == "blank":
        return blank_assessment_definition()
    raise KeyError(key)


def structural_signature(definition: AssessmentSystemDefinition) -> dict:
    return {
        "activities": [{
            "key": activity.key,
            "scoring": activity.scoring,
            "criteria": [{
                "key": criterion.key,
                "inputType": criterion.inputType,
                "dimensionKey": criterion.dimensionKey,
                "weight": str(criterion.weight),
                "minimum": str(criterion.minimum),
                "maximum": str(criterion.maximum),
                "step": str(criterion.step),
                "target": str(criterion.target) if criterion.target is not None else None,
                "direction": criterion.direction,
            } for criterion in activity.criteria],
            "assignment": {
                "mode": activity.assignment.mode,
                "maximumAssessors": activity.assignment.maximumAssessors,
                "avoidRepeatPairs": activity.assignment.avoidRepeatPairs,
                "predecessor": activity.assignment.predecessor,
                "reuse": activity.assignment.reuseAssignmentsFrom,
                "parallel": activity.assignment.parallelGroup,
                "mandatoryPlacements": activity.assignment.mandatoryPlacements,
            },
        } for activity in definition.activities if activity.enabled],
        "assessorCategories": [{
            "key": item.key,
            "primaryPriority": item.primaryPriority,
            "secondaryPriority": item.secondaryPriority,
        } for item in definition.assessors.categories],
        "dimensions": [{"key": item.key, "source": item.source, "activityKey": item.activityKey}
                       for item in definition.dimensions],
        "generalFactors": [item.storageKey for item in definition.generalFactors],
        "components": [{"source": item.source, "key": item.key, "weight": str(item.weight)}
                       for item in definition.scoring.components],
        "officialMaximum": str(definition.scoring.officialMaximum),
    }


def historical_impact(db: Session, system: AssessmentSystem, candidate: AssessmentSystemDefinition) -> dict:
    current = published_definition(db, system)
    session_count = db.scalar(select(func.count()).select_from(Journey)) or 0
    structural = structural_signature(current) != structural_signature(candidate)
    return {
        "sessionCount": session_count,
        "structuralChange": structural,
        "blocked": bool(session_count and structural),
        "message": (
            "This changes activities or scoring while saved Sessions exist. Export or use a fresh local database before publishing it."
            if session_count and structural else
            "Existing saved results remain compatible with this change."
        ),
    }


def save_draft(
    db: Session,
    system: AssessmentSystem,
    definition: AssessmentSystemDefinition,
    *,
    base_version: int,
    actor_name: str,
) -> AssessmentSystem:
    if system.version != base_version:
        raise ValueError("stale")
    system.name = definition.name
    system.draft_json = _definition_json(definition)
    system.updated_by = actor_name
    system.version += 1
    db.flush()
    return system


def publish_definition(
    db: Session,
    system: AssessmentSystem,
    definition: AssessmentSystemDefinition,
    *,
    base_version: int,
    actor_name: str,
    change_summary: str,
) -> AssessmentSystemVersion:
    if system.version != base_version:
        raise ValueError("stale")
    impact = historical_impact(db, system, definition)
    if impact["blocked"]:
        raise ValueError("historical")
    version = system.published_version + 1
    record = AssessmentSystemVersion(
        system_id=system.id,
        version=version,
        definition_json=_definition_json(definition),
        change_summary=change_summary.strip(),
        published_by=actor_name,
    )
    db.add(record)
    system.name = definition.name
    system.draft_json = record.definition_json
    system.published_version = version
    system.updated_by = actor_name
    system.version += 1
    allowed_categories = {item.key for item in definition.assessors.categories}
    fallback_category = sorted(definition.assessors.categories,
                               key=lambda item: (item.secondaryPriority, item.name.casefold()))[0].key
    for account in db.scalars(select(UserAccount)):
        if account.evaluator_role not in allowed_categories:
            account.evaluator_role = fallback_category
    for directory in db.scalars(select(EvaluatorDirectory)):
        if directory.default_role not in allowed_categories:
            directory.default_role = fallback_category
    for evaluator in db.scalars(select(Evaluator)):
        if evaluator.role not in allowed_categories:
            evaluator.role = fallback_category
    db.flush()
    activate_assessment_definition(definition)
    return record
