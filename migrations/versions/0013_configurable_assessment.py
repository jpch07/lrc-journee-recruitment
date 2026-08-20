"""Add the simple configurable assessment-system definition.

Revision ID: 0013_configurable_assessment
Revises: 0012_evaluator_contacts
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op


revision = "0013_configurable_assessment"
down_revision = "0012_evaluator_contacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "user_accounts" in existing:
        account_columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("user_accounts")}
        if "can_evaluate" not in account_columns:
            with op.batch_alter_table("user_accounts") as batch:
                batch.add_column(sa.Column("can_evaluate", sa.Boolean(), nullable=False, server_default=sa.true()))
    if "assessment_systems" not in existing:
        op.create_table(
            "assessment_systems",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="active"),
            sa.Column("published_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("draft_json", sa.Text(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("updated_by", sa.String(200), nullable=False, server_default="System"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "assessment_system_versions" not in existing:
        op.create_table(
            "assessment_system_versions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("system_id", sa.String(36), sa.ForeignKey("assessment_systems.id", ondelete="CASCADE"), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("definition_json", sa.Text(), nullable=False),
            sa.Column("change_summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("published_by", sa.String(200), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("system_id", "version", name="uq_assessment_system_version"),
        )

    # Production reaches this revision with populated Journee tables but no
    # assessment-system row. Seed the exact LRC compatibility definition before
    # the tenancy migration makes system_id mandatory on those existing rows.
    bind = op.get_bind()
    if not bind.execute(sa.text("select id from assessment_systems limit 1")).scalar():
        from app.assessment_config import lrc_assessment_definition

        system_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        definition = lrc_assessment_definition()
        definition_json = json.dumps(definition.model_dump(mode="json"), separators=(",", ":"))
        bind.execute(sa.text(
            "insert into assessment_systems "
            "(id, name, status, published_version, draft_json, version, updated_by, created_at, updated_at) "
            "values (:id, :name, 'active', 1, :definition, 1, :actor, :created_at, :updated_at)"
        ), {
            "id": system_id,
            "name": definition.name,
            "definition": definition_json,
            "actor": "System migration",
            "created_at": now,
            "updated_at": now,
        })
        bind.execute(sa.text(
            "insert into assessment_system_versions "
            "(id, system_id, version, definition_json, change_summary, published_by, created_at) "
            "values (:id, :system_id, 1, :definition, :summary, :actor, :created_at)"
        ), {
            "id": str(uuid.uuid4()),
            "system_id": system_id,
            "definition": definition_json,
            "summary": "Finalized LRC system compatibility preset.",
            "actor": "System migration",
            "created_at": now,
        })


def downgrade() -> None:
    op.drop_table("assessment_system_versions")
    op.drop_table("assessment_systems")
    with op.batch_alter_table("user_accounts") as batch:
        batch.drop_column("can_evaluate")
