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


def _has_values_column(bind) -> bool:
    if bind.dialect.name == "cockroachdb":
        # Cockroach's dialect inspector can wait for an earlier schema-change
        # lease to be garbage-collected. information_schema is immediate and
        # also makes a partially completed deployment safely resumable.
        return bool(bind.execute(sa.text(
            "select count(*) from information_schema.columns "
            "where table_schema = 'journee_recruitment' "
            "and table_name = 'general_assessments' and column_name = 'values_json'"
        )).scalar())
    return "values_json" in {
        item["name"] for item in sa.inspect(bind).get_columns("general_assessments")
    }


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_values_column(bind):
        op.add_column(
            "general_assessments",
            sa.Column("values_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        )

    # Do not reflect this table immediately after adding the column. CockroachDB
    # publishes schema changes asynchronously, and dialect reflection can wait
    # on the schema-change lease even though the new column is already usable.
    # Explicit SQL keeps this migration quick and safe on CockroachDB, PostgreSQL,
    # and SQLite, including a retry after a partially completed DDL operation.
    rows = bind.execute(sa.text(
        "select recruit_id, values_json, punctuality, respect, seriousness "
        "from general_assessments"
    )).mappings().all()
    update_row = sa.text(
        "update general_assessments set values_json = :values_json "
        "where recruit_id = :recruit_id"
    )
    for row in rows:
        try:
            existing = json.loads(row.get("values_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            existing = {}
        if not isinstance(existing, dict):
            existing = {}
        for key in LEGACY_KEYS:
            if key not in existing:
                existing[key] = _json_number(row.get(key))
        bind.execute(update_row, {
            "recruit_id": row["recruit_id"],
            "values_json": json.dumps(existing, ensure_ascii=False, separators=(",", ":")),
        })


def downgrade() -> None:
    if _has_values_column(op.get_bind()):
        op.drop_column("general_assessments", "values_json")
