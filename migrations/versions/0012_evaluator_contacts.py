"""Add evaluator full names and phone numbers.

Revision ID: 0012_evaluator_contacts
Revises: 0011_recruit_directory
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0012_evaluator_contacts"
down_revision = "0011_recruit_directory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("evaluator_directory")}
    with op.batch_alter_table("evaluator_directory") as batch:
        if "full_name" not in columns:
            batch.add_column(sa.Column("full_name", sa.String(length=200), nullable=True))
        if "phone_number" not in columns:
            batch.add_column(sa.Column("phone_number", sa.String(length=40), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("evaluator_directory") as batch:
        batch.drop_column("phone_number")
        batch.drop_column("full_name")
