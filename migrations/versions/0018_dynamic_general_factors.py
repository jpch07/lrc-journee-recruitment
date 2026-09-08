"""Store configurable general-assessment factor values.

Revision ID: 0018_dynamic_general_factors
Revises: 0017_room_evaluator_locks
"""
from __future__ import annotations

import json
from decimal import Decimal

import sqlalchemy as sa
from alembic import op


revision = "0018_dynamic_general_factors"
down_revision = "0017_room_evaluator_locks"
branch_labels = None
depends_on = None


LEGACY_KEYS = ("punctuality", "respect", "seriousness")


def _json_number(value: object) -> float | None:
    if value is None:
        return None
    return float(Decimal(str(value)))


def upgrade() -> None:
    bind = op.get_bind()
    columns = {item["name"] for item in sa.inspect(bind).get_columns("general_assessments")}
    if "values_json" not in columns:
        op.add_column(
            "general_assessments",
            sa.Column("values_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        )

    metadata = sa.MetaData()
    assessments = sa.Table("general_assessments", metadata, autoload_with=bind)
    for row in bind.execute(sa.select(assessments)).mappings():
        try:
            existing = json.loads(row.get("values_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            existing = {}
        if not isinstance(existing, dict):
            existing = {}
        for key in LEGACY_KEYS:
            if key not in existing:
                existing[key] = _json_number(row.get(key))
        bind.execute(
            assessments.update()
            .where(assessments.c.recruit_id == row["recruit_id"])
            .values(values_json=json.dumps(existing, ensure_ascii=False, separators=(",", ":")))
        )


def downgrade() -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("general_assessments")}
    if "values_json" in columns:
        op.drop_column("general_assessments", "values_json")
