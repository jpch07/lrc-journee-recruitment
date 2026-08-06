"""add scoped recruit attendance access

Revision ID: 0007_recruit_attendance_access
Revises: 0006_evaluator_master_directory
"""

from datetime import datetime, timezone
import secrets

from alembic import op
import sqlalchemy as sa


revision = "0007_recruit_attendance_access"
down_revision = "0006_evaluator_master_directory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if not inspector.has_table("recruit_attendance_access"):
        op.create_table(
            "recruit_attendance_access",
            sa.Column("journey_id", sa.String(length=36), nullable=False),
            sa.Column("token", sa.String(length=80), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["journey_id"], ["journeys.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("journey_id"),
            sa.UniqueConstraint("token"),
        )
    if not inspector.has_table("recruit_attendance_sessions"):
        op.create_table(
            "recruit_attendance_sessions",
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("journey_id", sa.String(length=36), nullable=False),
            sa.Column("actor_name", sa.String(length=200), nullable=False),
            sa.Column("csrf_token", sa.String(length=80), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["journey_id"], ["journeys.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("token_hash"),
        )

    metadata = sa.MetaData()
    journeys = sa.Table("journeys", metadata, autoload_with=connection)
    access = sa.Table("recruit_attendance_access", metadata, autoload_with=connection)
    now = datetime.now(timezone.utc)
    existing = set(connection.execute(sa.select(access.c.journey_id)).scalars())
    for journey_id in connection.execute(sa.select(journeys.c.id)).scalars():
        if journey_id not in existing:
            connection.execute(
                access.insert().values(
                    journey_id=journey_id,
                    token=secrets.token_urlsafe(32),
                    created_at=now,
                    rotated_at=None,
                )
            )


def downgrade() -> None:
    op.drop_table("recruit_attendance_sessions")
    op.drop_table("recruit_attendance_access")
