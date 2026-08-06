"""Add notes to general assessments.

Revision ID: 0008_general_assessment_notes
Revises: 0007_recruit_attendance_access
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0008_general_assessment_notes"
down_revision = "0007_recruit_attendance_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("general_assessments")}
    if "notes" not in columns:
        op.add_column(
            "general_assessments",
            sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("general_assessments")}
    if "notes" in columns:
        op.drop_column("general_assessments", "notes")
