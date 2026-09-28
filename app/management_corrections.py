"""Pure, scale-aware management adjustments. Never edits evaluator inputs."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import hashlib
import json


def criterion_scale(activity, criterion):
    return (Decimal(0), Decimal(5)) if activity.scoring == 'target_average' else (Decimal(criterion.minimum), Decimal(criterion.maximum))


def resolve_targets(definition, level: str, key: str, activity_key: str | None = None) -> list[tuple[str, str]]:
    activities = [a for a in definition.activities if a.enabled]
    if level == 'criterion':
        targets = [(a.key, c.key) for a in activities for c in a.criteria
                   if a.key == activity_key and c.key == key]
    elif level == 'activity':
        targets = [(a.key, c.key) for a in activities for c in a.criteria
                   if a.key == key and c.weight > 0]
    elif level == 'dimension':
        dimension = next((d for d in definition.dimensions if d.key == key), None)
        targets = [] if dimension is None else [
            (a.key, c.key) for a in activities for c in a.criteria if c.weight > 0 and
            ((dimension.source == 'activity' and a.key == dimension.activityKey) or
             (dimension.source == 'criteria' and c.dimensionKey == key))]
    else:
        targets = []
    if not targets:
        raise ValueError('This target has no available contributing criteria. Check the assessment configuration.')
    return targets


def normalize_target(value: Decimal, minimum: Decimal, maximum: Decimal) -> Decimal:
    if not value.is_finite() or maximum <= minimum or not minimum <= value <= maximum:
        raise ValueError('Grade is outside its configured scale.')
    return (value - minimum) / (maximum - minimum)


def build_override_map(definition, current: dict, level: str, key: str, value: Decimal,
                       activity_key: str | None = None) -> dict:
    targets = resolve_targets(definition, level, key, activity_key)
    minimum, maximum = Decimal(0), Decimal(5)
    if level == 'dimension':
        maximum = next(d.displayMaximum for d in definition.dimensions if d.key == key)
    elif level == 'criterion':
        activity = next(a for a in definition.activities if a.key == activity_key)
        criterion = next(c for c in activity.criteria if c.key == key)
        minimum, maximum = criterion_scale(activity, criterion)
    normalized = str(normalize_target(value, minimum, maximum))
    updated = deepcopy(current)
    for activity, criterion in targets:
        updated.setdefault(activity, {})[criterion] = normalized
    return updated


def correction_configuration(definition) -> dict:
    return {
        'activities': [{
            'key': a.key, 'enabled': a.enabled, 'scoring': a.scoring,
            'criteria': [{k: c.model_dump(mode='json')[k] for k in
                         ('key', 'minimum', 'maximum', 'weight', 'target', 'direction', 'dimensionKey')}
                         for c in a.criteria],
        } for a in definition.activities],
        'dimensions': [{k: d.model_dump(mode='json')[k] for k in
                        ('key', 'source', 'activityKey', 'displayMaximum')} for d in definition.dimensions],
        'scoring': definition.scoring.model_dump(mode='json', exclude={'bands'}),
        'bands': sorted(b.key for b in definition.scoring.bands),
    }


def correction_signature(definition) -> str:
    return hashlib.sha256(json.dumps(correction_configuration(definition), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate_override_map(definition, values: dict) -> None:
    if not isinstance(values, dict):
        raise ValueError('Invalid correction data.')
    for activity_key, criteria in values.items():
        if not isinstance(criteria, dict):
            raise ValueError('Invalid criterion corrections.')
        for key, value in criteria.items():
            resolve_targets(definition, 'criterion', key, activity_key)
            normalize_target(Decimal(str(value)), Decimal(0), Decimal(1))
