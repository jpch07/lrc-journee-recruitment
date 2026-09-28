"""Scoped correction state and atomic operations (never changes submissions)."""
from copy import deepcopy
import hashlib
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .assessment_runtime import active_assessment_definition
from .management_corrections import (build_override_map, correction_signature, correction_configuration, criterion_scale,
                                     resolve_targets, validate_override_map)
from .models import ManagementCorrection, AuditEvent, utcnow
from .utils import loads, dumps, audit


def read_corrections(db, journey, recruit) -> dict:
    record = db.scalar(select(ManagementCorrection).where(
        ManagementCorrection.system_id == journey.system_id,
        ManagementCorrection.journey_id == journey.id,
        ManagementCorrection.recruit_id == recruit.id,
    ))
    definition = active_assessment_definition()
    signature = correction_signature(definition)
    targets = []
    for activity in (a for a in definition.activities if a.enabled):
        targets.append({'level': 'activity', 'key': activity.key, 'name': activity.name, 'minimum': 0, 'maximum': 5})
        for criterion in activity.criteria:
            minimum, maximum = criterion_scale(activity, criterion)
            targets.append({'level': 'criterion', 'key': criterion.key, 'activityKey': activity.key,
                            'name': f'{activity.name} — {criterion.name}', 'minimum': float(minimum), 'maximum': float(maximum)})
    targets.extend({'level': 'dimension', 'key': d.key, 'name': d.name, 'minimum': 0,
                    'maximum': float(d.displayMaximum)} for d in definition.dimensions)
    return {
        'targets': targets,
        'bands': [{'key': b.key, 'name': b.name} for b in definition.scoring.bands],
        'revision': record.revision if record else 0,
        'values': loads(record.criterion_values_json, {}) if record else {},
        'color': record.color_key if record else None,
        'configurationSignature': signature,
        'conflict': bool(record and record.configuration_signature != signature and
                         (loads(record.criterion_values_json, {}) or record.color_key)),
        'history': [{'id': item.id, 'actorName': item.actor_name, 'createdAt': item.created_at.isoformat(),
                     'reason': item.reason, 'before': loads(item.before_json, {}), 'after': loads(item.after_json, {})}
                    for item in db.scalars(select(AuditEvent).where(
                        AuditEvent.system_id == journey.system_id, AuditEvent.journey_id == journey.id,
                        AuditEvent.entity_id == recruit.id, AuditEvent.action == 'management.correction'
                    ).order_by(AuditEvent.created_at.desc()))],
    }


def preview_correction(db, journey, recruit, operation) -> dict:
    from .services import result_snapshot
    state = read_corrections(db, journey, recruit)
    definition = active_assessment_definition()
    if state['revision'] != operation.revision or state['configurationSignature'] != operation.configurationSignature:
        raise HTTPException(409, 'Grades or configuration changed elsewhere. Reload before continuing.')
    if state['conflict']:
        raise HTTPException(409, 'Saved corrections use a different scoring configuration. Restore that configuration for review.')
    changed = {'values': deepcopy(state['values']), 'color': state['color']}
    targets = []
    try:
        if operation.action == 'undo':
            event = next((e for e in state['history'] if e['id'] == operation.eventId), None)
            if not event:
                raise ValueError('Correction history entry not found for this recruit.')
            if event['before'].get('configurationSignature') != state['configurationSignature']:
                raise ValueError('This history entry uses a different configuration.')
            changed = {k: deepcopy(event['before'][k]) for k in ('values', 'color')}
        elif operation.level == 'color':
            if operation.value is not None:
                raise ValueError('Color corrections cannot change numerical grades.')
            if operation.action == 'restore':
                changed['color'] = None
            elif operation.key in {b.key for b in definition.scoring.bands}:
                changed['color'] = operation.key
            else:
                raise ValueError('Select a configured color grade.')
        else:
            targets = resolve_targets(definition, operation.level, operation.key, operation.activityKey)
            if operation.action == 'restore':
                if operation.value is not None:
                    raise ValueError('Restore does not take a numerical value.')
                for code, key in targets:
                    changed['values'].get(code, {}).pop(key, None)
                changed['values'] = {k: v for k, v in changed['values'].items() if v}
            else:
                if operation.value is None:
                    raise ValueError('Enter a target grade.')
                changed['values'] = build_override_map(definition, changed['values'], operation.level,
                                                      operation.key, operation.value, operation.activityKey)
        validate_override_map(definition, changed['values'])
        if changed['color'] and changed['color'] not in {b.key for b in definition.scoring.bands}:
            raise ValueError('That color grade is no longer configured.')
    except (ValueError, ArithmeticError) as exc:
        raise HTTPException(422, str(exc)) from exc
    before = next((r for r in result_snapshot(db, journey, include_criteria=True)['rows'] if r['recruitId'] == recruit.id), None)
    if before is None:
        raise HTTPException(422, 'Only active, present recruits with a result can be corrected.')
    after = next(r for r in result_snapshot(db, journey, correction_overrides={recruit.id: changed}, include_criteria=True)['rows'] if r['recruitId'] == recruit.id)
    if operation.action == 'undo':
        targets = [(a.key, c.key) for a in definition.activities if a.enabled for c in a.criteria
                   if before['criteria'][a.key][c.key] != after['criteria'][a.key][c.key]]
    affected = [{'activityKey': code, 'key': key, 'before': before['criteria'][code][key],
                 'after': after['criteria'][code][key]} for code, key in targets]
    warnings = []
    if any(c['before']['automaticAverage'] is None for c in affected):
        warnings.append('Some criteria have no automatic grade. This correction does not create evaluator submissions.')
    if any(c['before']['adjusted'] for c in affected):
        warnings.append('Existing corrections for the listed criteria will be replaced or restored.')
    fingerprint = hashlib.sha256(dumps({'row': before, 'revision': state['revision'],
                                       'signature': state['configurationSignature']}).encode()).hexdigest()
    return {'before': before, 'after': after, 'affectedCriteria': affected, 'warnings': warnings,
            'inputFingerprint': fingerprint, 'revision': state['revision'],
            'configurationSignature': state['configurationSignature'], 'changed': changed}


def apply_correction(db, journey, recruit, operation, actor_name: str, actor_type: str) -> dict:
    try:
        preview = preview_correction(db, journey, recruit, operation)
        if not operation.inputFingerprint or operation.inputFingerprint != preview['inputFingerprint']:
            raise HTTPException(409, 'Grades changed or preview is missing. Preview the changes again before saving.')
        state = read_corrections(db, journey, recruit)
        changed = preview['changed']
        fields = {'criterion_values_json': dumps(changed['values']), 'color_key': changed['color'],
                  'configuration_signature': state['configurationSignature'],
                  'configuration_json': dumps(correction_configuration(active_assessment_definition())),
                  'revision': state['revision'] + 1, 'updated_at': utcnow(), 'updated_by': actor_name}
        if state['revision'] == 0:
            db.add(ManagementCorrection(system_id=journey.system_id, journey_id=journey.id, recruit_id=recruit.id, **fields))
            db.flush()
        else:
            result = db.execute(update(ManagementCorrection).where(
                ManagementCorrection.system_id == journey.system_id, ManagementCorrection.journey_id == journey.id,
                ManagementCorrection.recruit_id == recruit.id, ManagementCorrection.revision == operation.revision
            ).values(**fields))
            if result.rowcount != 1:
                raise HTTPException(409, 'Another manager saved first. Reload the latest grades.')
        audit(db, journey_id=journey.id, actor_type=actor_type, actor_name=actor_name,
              action='management.correction', entity_type='recruit', entity_id=recruit.id,
              before={**{k: state[k] for k in ('values', 'color', 'revision', 'configurationSignature')},
                      'overallScore': preview['before']['overallScore']},
              after={**changed, 'revision': fields['revision'], 'configurationSignature': state['configurationSignature'],
                     'overallScore': preview['after']['overallScore'], 'operation': operation.model_dump(mode='json'),
                     'affectedCriteria': preview['affectedCriteria']}, reason=operation.reason.strip())
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, 'Another manager saved first. Reload the latest grades.') from exc
    except Exception:
        db.rollback()
        raise
    return {'saved': True, 'revision': fields['revision'], 'result': preview['after']}
