"""Add named accounts, permissions, admin evaluations, and attendance comments.

Revision ID: 0009_accounts_admin_evaluations
Revises: 0008_general_assessment_notes
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0009_accounts_admin_evaluations"
down_revision = "0008_general_assessment_notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    recruit_columns = {column["name"] for column in inspector.get_columns("recruits")}
    if "attendance_comment" not in recruit_columns:
        op.add_column("recruits", sa.Column("attendance_comment", sa.Text(), nullable=False, server_default=""))

    tables = set(inspector.get_table_names())
    if "user_accounts" not in tables:
        op.create_table(
            "user_accounts",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("username", sa.String(200), nullable=False, unique=True),
            sa.Column("password_hash", sa.String(500), nullable=False),
            sa.Column("directory_id", sa.String(36), sa.ForeignKey("evaluator_directory.id"), nullable=True, unique=True),
            sa.Column("evaluator_role", sa.String(20), nullable=False, server_default="dossard"),
            sa.Column("is_owner", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("can_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("can_results", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "journey_permissions" not in tables:
        op.create_table(
            "journey_permissions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("account_id", sa.String(36), sa.ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False),
            sa.Column("journey_id", sa.String(36), sa.ForeignKey("journeys.id", ondelete="CASCADE"), nullable=False),
            sa.Column("can_attendance", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("account_id", "journey_id", name="uq_account_journey_permission"),
        )
    if "user_sessions" not in tables:
        op.create_table(
            "user_sessions",
            sa.Column("token_hash", sa.String(64), primary_key=True),
            sa.Column("account_id", sa.String(36), sa.ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False),
            sa.Column("csrf_token", sa.String(80), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "admin_evaluations" not in tables:
        op.create_table(
            "admin_evaluations",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("journey_id", sa.String(36), sa.ForeignKey("journeys.id", ondelete="CASCADE"), nullable=False),
            sa.Column("recruit_id", sa.String(36), sa.ForeignKey("recruits.id", ondelete="CASCADE"), nullable=False),
            sa.Column("activity_code", sa.String(30), nullable=False),
            sa.Column("responses_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("raw_payload_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("comments", sa.Text(), nullable=False, server_default=""),
            sa.Column("score", sa.Numeric(8, 5), nullable=False, server_default="0"),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("updated_by", sa.String(200), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("journey_id", "recruit_id", "activity_code", name="uq_admin_evaluation_recruit_activity"),
        )
        op.create_index("ix_admin_evaluation_journey_activity", "admin_evaluations", ["journey_id", "activity_code"])

    directory = sa.Table("evaluator_directory", sa.MetaData(), autoload_with=bind)
    bind.execute(
        directory.update().where(sa.func.lower(directory.c.name) == "marita").values(active=False)
    )


def downgrade() -> None:
    op.drop_table("admin_evaluations")
    op.drop_table("user_sessions")
    op.drop_table("journey_permissions")
    op.drop_table("user_accounts")
    op.drop_column("recruits", "attendance_comment")
