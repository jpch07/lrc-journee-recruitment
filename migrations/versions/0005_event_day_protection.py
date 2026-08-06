"""add event day protection control

Revision ID: 0005_event_day_protection
Revises: 0004_recruit_date_of_birth
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_event_day_protection"
down_revision = "0004_recruit_date_of_birth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("event_day_protections"):
        return
    op.create_table(
        "event_day_protections",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("journey_id", sa.String(length=36), nullable=False),
        sa.Column("activation_id", sa.String(length=36), nullable=False),
        sa.Column("duration_hours", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("github_run_id", sa.String(length=40), nullable=True),
        sa.Column("github_run_url", sa.String(length=500), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["journey_id"], ["journeys.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("activation_id"),
        sa.UniqueConstraint("journey_id", name="uq_event_day_protection_journey"),
    )


def downgrade() -> None:
    op.drop_table("event_day_protections")
