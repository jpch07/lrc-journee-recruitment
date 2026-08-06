"""Add recruit phone numbers.

Revision ID: 0003_recruit_phone
Revises: 0002_rubric_snapshot
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_recruit_phone"
down_revision = "0002_rubric_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("recruits")}
    if "phone_number" not in columns:
        op.add_column("recruits", sa.Column("phone_number", sa.String(length=40), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("recruits")}
    if "phone_number" in columns:
        op.drop_column("recruits", "phone_number")
