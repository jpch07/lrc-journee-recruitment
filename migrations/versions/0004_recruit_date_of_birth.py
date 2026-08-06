"""Add recruit dates of birth.

Revision ID: 0004_recruit_date_of_birth
Revises: 0003_recruit_phone
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0004_recruit_date_of_birth"
down_revision = "0003_recruit_phone"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("recruits")}
    if "date_of_birth" not in columns:
        op.add_column("recruits", sa.Column("date_of_birth", sa.Date(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("recruits")}
    if "date_of_birth" in columns:
        op.drop_column("recruits", "date_of_birth")
