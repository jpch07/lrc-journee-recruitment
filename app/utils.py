from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from .models import AuditEvent


def json_default(value: Any):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"Cannot serialize {type(value)!r}")


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=json_default)


def loads(value: str | None, default: Any):
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def audit(
    db: Session,
    *,
    journey_id: str | None,
    actor_type: str,
    actor_name: str,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    before: Any = None,
    after: Any = None,
    reason: str = "",
) -> None:
    # Keep human identity alongside future evidence, without changing scoring fields.
    if isinstance(after, dict) or after is None:
        from sqlalchemy import select
        from .models import Journey, Recruit, Evaluator
        context = {}
        with db.no_autoflush:
            for model, identifier, key in ((Journey, journey_id, "journeyName"),
                ({"recruit": Recruit, "evaluator": Evaluator, "journey": Journey}.get(entity_type), entity_id, "entityName")):
                if model is not None and identifier:
                    pending = next((row for row in (*db.new, *db.identity_map.values())
                                    if isinstance(row, model) and row.__dict__.get('id') == identifier
                                    and 'name' in row.__dict__), None)
                    query = select(model.name).where(model.id == identifier)
                    if model is not Journey:
                        query = query.join(Journey, Journey.id == model.journey_id)
                    name = pending.name if pending else db.scalar(query)
                    if name:
                        context[key] = name
        if context:
            after = {**(after or {}), "_auditContext": context}
    db.add(
        AuditEvent(
            journey_id=journey_id,
            actor_type=actor_type,
            actor_name=actor_name,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before_json=dumps(before or {}),
            after_json=dumps(after or {}),
            reason=reason,
        )
    )

