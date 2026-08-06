"""Add immutable per-Journee rubric snapshots.

Revision ID: 0002_rubric_snapshot
Revises: 0001_initial
"""
from __future__ import annotations

from alembic import op

from app.db import Base
from app import models  # noqa: F401


revision = "0002_rubric_snapshot"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.tables["rubric_snapshots"].create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.tables["rubric_snapshots"].drop(bind=op.get_bind(), checkfirst=True)
