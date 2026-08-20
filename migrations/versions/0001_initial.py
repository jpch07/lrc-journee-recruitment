"""Initial Journee Recruitment schema.

Revision ID: 0001_initial
Revises:
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

from app.db import Base
from app import models  # noqa: F401


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)
    # Base.metadata reflects the current model when a brand-new database is
    # created. Seed its required compatibility workspace so older data-seeding
    # revisions can run against that current schema. Existing installations
    # have already passed this revision and are unaffected.
    inspector = sa.inspect(bind)
    if inspector.has_table("assessment_systems") and not bind.execute(
        sa.text("select id from assessment_systems limit 1")
    ).scalar():
        from app.assessment_config import lrc_assessment_definition

        system_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        definition = lrc_assessment_definition()
        definition_json = json.dumps(definition.model_dump(mode="json"), separators=(",", ":"))
        columns = {item["name"] for item in inspector.get_columns("assessment_systems")}
        values = {
            "id": system_id,
            "name": definition.name,
            "status": "active",
            "published_version": 1,
            "draft_json": definition_json,
            "version": 1,
            "updated_by": "System bootstrap",
            "created_at": now,
            "updated_at": now,
        }
        if "slug" in columns:
            values["slug"] = "lrc-journee-recruitment-2026"
        systems = sa.Table("assessment_systems", sa.MetaData(), autoload_with=bind)
        bind.execute(systems.insert().values(**values))
        versions = sa.Table("assessment_system_versions", sa.MetaData(), autoload_with=bind)
        bind.execute(versions.insert().values(
            id=str(uuid.uuid4()), system_id=system_id, version=1,
            definition_json=definition_json,
            change_summary="Finalized LRC system compatibility preset.",
            published_by="System bootstrap", created_at=now,
        ))


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
