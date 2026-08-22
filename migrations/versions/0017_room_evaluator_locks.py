"""Add manual evaluator locks to activity room plans.

Revision ID: 0017_room_evaluator_locks
Revises: 0016_activity_operations_r2
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0017_room_evaluator_locks"
down_revision = "0016_activity_operations_r2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("room_plan_evaluators")}
    if "locked" not in columns:
        op.add_column(
            "room_plan_evaluators",
            sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    op.drop_column("room_plan_evaluators", "locked")
