"""Read-only audit presentation. Stored evidence is never rewritten."""
from __future__ import annotations

import re
from sqlalchemy import select
from .models import Journey, Recruit, Evaluator
from .utils import loads


def label(value):
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(value)).replace("_", " ").replace(".", " · ").capitalize()


def present_events(db, events):
    from .assessment_runtime import active_assessment_definition
    definition = active_assessment_definition()
    activity_names = {activity.key: activity.name for activity in definition.activities}
    events = list(events)
    journeys = {e.journey_id for e in events if e.journey_id}
    entities = {e.entity_id for e in events if e.entity_id}
    systems = {e.system_id for e in events}
    names = {}
    for model, kind in ((Recruit, "recruit"), (Evaluator, "evaluator"), (Journey, "journey")):
        ids = journeys | entities if kind == "journey" else entities
        if ids:
            query = select(Journey.system_id, model.id, model.name)
            if model is not Journey:
                query = query.join(Journey, Journey.id == model.journey_id)
            query = query.where(model.id.in_(ids), Journey.system_id.in_(systems))
            for sid, rid, name in db.execute(query):
                names[(sid, kind, rid)] = name
    result = []
    for event in events:
        before, after = loads(event.before_json, {}), loads(event.after_json, {})
        old = before if isinstance(before, dict) else {}
        new = after if isinstance(after, dict) else {}
        snapshot = new.get("_auditContext") or {}
        person = snapshot.get("entityName") or new.get("recruitName") or old.get("recruitName")
        if not person:
            person = names.get((event.system_id, event.entity_type, event.entity_id))
        if not person and event.entity_type in ("recruit", "evaluator", "journey"):
            person = new.get("name") or old.get("name") or f"{label(event.entity_type)} no longer available"
        journey = snapshot.get("journeyName") or names.get((event.system_id, "journey", event.journey_id))
        changes, criteria = [], []
        operation = new.get("operation", {})
        operation = operation if isinstance(operation, dict) else {}
        title = {"recruit.profile_updated": "Updated general assessment",
                 "recruit.added": "Added recruit", "recruit.photo_updated": "Updated profile photo",
                 "evaluation.admin_override_saved": "Saved official management evaluation",
                 "evaluation.admin_override_removed": "Removed official management evaluation",
                 "assignments.manually_edited": "Edited assignments",
                 "room_plan.manually_edited": "Edited room distribution",
                 "journey.deleted": "Deleted Journee", "journey.created": "Created Journee"}.get(event.action, label(event.action))
        if event.action == "management.correction":
            level = operation.get("level", "grade")
            subject = activity_names.get(operation.get("key"), label(operation.get("key", ""))) if level == "activity" else label(level)
            verb = {"restore": "Restored automatic", "undo": "Undid correction to"}.get(operation.get("action"), "Corrected")
            title = f"{verb} {subject.lower() if level != 'activity' else subject}".strip()
            for key, caption in (("overallScore", "Overall score"), ("color", "Color grade")):
                if old.get(key) != new.get(key):
                    changes.append({"label": caption, "before": old.get(key), "after": new.get(key)})
            for item in new.get("affectedCriteria", []):
                left, right = item.get("before") or {}, item.get("after") or {}
                if left.get("effectiveAverage") != right.get("effectiveAverage"):
                    criteria.append({"label": right.get("name") or left.get("name") or label(item.get("key", "Criterion")),
                                     "activity": activity_names.get(item.get("activityKey"), label(item.get("activityKey", ""))),
                                     "before": left.get("effectiveAverage"), "after": right.get("effectiveAverage"),
                                     "maximum": right.get("maximum") or left.get("maximum")})
        else:
            factors = {factor['key']: factor for factor in old.get('factors', []) if isinstance(factor, dict) and 'key' in factor}
            factors.update({factor['key']: factor for factor in new.get('factors', []) if isinstance(factor, dict) and 'key' in factor})
            for key, factor in factors.items():
                previous, updated = old.get('values', {}).get(key), new.get('values', {}).get(key)
                if previous != updated:
                    changes.append({'label': factor.get('name') or label(key), 'before': previous, 'after': updated})
            # Only human-facing scalar fields: never expose password/hash/token values.
            for key in ("name", "status", "present", "arrivalTime", "comment", "notes", "punctuality", "respect", "seriousness", "roomCount", "active", "canAdmin", "canResults"):
                if key not in factors and old.get(key) != new.get(key) and not isinstance(new.get(key), (dict, list)):
                    changes.append({"label": label(key), "before": old.get(key), "after": new.get(key)})
        result.append({"id": event.id, "actorType": event.actor_type, "actorName": event.actor_name,
                       "action": event.action, "entityType": event.entity_type, "entityId": event.entity_id,
                       "entityName": person, "journeyName": journey, "journeyId": event.journey_id,
                       "title": title, "changes": changes, "criteriaChanges": criteria,
                       "reason": event.reason, "createdAt": event.created_at.isoformat()})
    return result
