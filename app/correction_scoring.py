"""Criterion provenance and effective overlays shared by preview and results."""
from decimal import Decimal

from .management_corrections import criterion_scale, resolve_targets


def criterion_facts(definition, responses, admin_responses, expected, overrides):
    facts = {}
    for activity in (a for a in definition.activities if a.enabled):
        facts[activity.key] = {}
        for criterion in activity.criteria:
            minimum, maximum = criterion_scale(activity, criterion)
            def grades(payloads):
                result = []
                for payload in payloads:
                    try:
                        value = Decimal(str(payload[criterion.key]))
                        if value.is_finite() and minimum <= value <= maximum:
                            result.append(value)
                    except (KeyError, TypeError, ValueError, ArithmeticError):
                        pass
                return result
            raw = grades(responses.get(activity.key, []))
            admin = admin_responses.get(activity.key)
            source = grades([admin]) if admin is not None else raw
            denominator = (expected.get(activity.key, 0)
                           if definition.scoring.assessorAggregation == 'missing_as_zero' and admin is None
                           else len(source))
            # Missing evaluations contribute zero achievement, not a raw grade
            # below the configured minimum. Normalize before missing-as-zero.
            normalized = (sum(((grade - minimum) / (maximum - minimum) for grade in source), Decimal(0))
                          / max(denominator, 1))
            automatic = minimum + normalized * (maximum - minimum) if source else None
            adjusted = overrides.get(activity.key, {}).get(criterion.key)
            effective_normalized = Decimal(adjusted) if adjusted is not None else normalized
            facts[activity.key][criterion.key] = {
                'name': criterion.name, 'minimum': float(minimum), 'maximum': float(maximum),
                'rawAverage': float(sum(raw, Decimal(0)) / len(raw)) if raw else None,
                'automaticAverage': float(automatic) if automatic is not None else None,
                'effectiveAverage': float(minimum + effective_normalized * (maximum - minimum)) if source or adjusted is not None else None,
                'adjusted': adjusted is not None,
                '_normalized': effective_normalized,
                '_available': bool(source) or adjusted is not None,
                '_weight': Decimal(criterion.weight),
            }
    return facts


def apply_numeric_overlays(definition, overrides, facts, activity_values, dimension_values,
                           activities_payload, dimensions_payload):
    for activity in (a for a in definition.activities if a.enabled):
        code = activity.key
        payload = activities_payload[code]
        payload['automaticScore'] = float(activity_values[code])
        payload['manuallyGraded'] = bool(overrides.get(code))
        if overrides.get(code):
            items = [facts[code][c.key] for c in activity.criteria if c.weight > 0]
            total = sum((c['_weight'] for c in items), Decimal(0))
            activity_values[code] = sum((c['_normalized'] * c['_weight'] for c in items), Decimal(0)) / total * 5
            payload['score'] = float(activity_values[code])
    ready = set()
    for dimension in definition.dimensions:
        payload = dimensions_payload[dimension.key]
        payload['automaticScore'] = float(dimension_values[dimension.key])
        try:
            targets = resolve_targets(definition, 'dimension', dimension.key)
        except ValueError:
            targets = []
        changed = any(key in overrides.get(code, {}) for code, key in targets)
        payload['manuallyGraded'] = changed
        if not changed:
            continue
        items = [facts[code][key] for code, key in targets]
        if dimension.source == 'activity':
            value = activity_values[dimension.activityKey] / 5
        else:
            value = sum((c['_normalized'] * c['_weight'] for c in items), Decimal(0)) / sum((c['_weight'] for c in items), Decimal(0))
        dimension_values[dimension.key] = value
        payload['score'] = float(value)
        if all(c['_available'] for c in items):
            ready.add(('dimension', dimension.key))
    return ready


def public_facts(facts):
    return {code: {key: {k: v for k, v in item.items() if not k.startswith('_')}
                   for key, item in criteria.items()} for code, criteria in facts.items()}
