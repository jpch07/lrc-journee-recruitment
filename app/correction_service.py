"""Scoped correction state and atomic operations (never changes submissions)."""
from sqlalchemy import select

from .assessment_runtime import active_assessment_definition
from .management_corrections import correction_signature
from .models import ManagementCorrection
from .utils import loads


def read_corrections(db, journey, recruit) -> dict:
    record = db.scalar(select(ManagementCorrection).where(
        ManagementCorrection.system_id == journey.system_id,
        ManagementCorrection.journey_id == journey.id,
        ManagementCorrection.recruit_id == recruit.id,
    ))
    signature = correction_signature(active_assessment_definition())
    return {
        'revision': record.revision if record else 0,
        'values': loads(record.criterion_values_json, {}) if record else {},
        'color': record.color_key if record else None,
        'configurationSignature': signature,
        'conflict': bool(record and record.configuration_signature != signature and
                         (loads(record.criterion_values_json, {}) or record.color_key)),
    }
