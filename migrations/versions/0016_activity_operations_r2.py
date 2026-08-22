"""Add activity-scoped operations and object photo metadata.

Revision ID: 0016_activity_operations_r2
Revises: 0015_global_owner_accounts
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
import json
from datetime import datetime, timezone
from uuid import uuid4


revision = "0016_activity_operations_r2"
down_revision = "0015_global_owner_accounts"
branch_labels = None
depends_on = None


def _columns(bind, table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(bind).get_columns(table)}


def _id() -> str:
    return str(uuid4())


def _backfill_activity_plans(bind) -> None:
    """Create independent historical activity snapshots without changing scores."""
    metadata = sa.MetaData()
    metadata.reflect(bind=bind, only=[
        "journeys", "evaluators", "mandatory_room_evaluators", "activity_operations",
        "activity_evaluator_availability", "activity_mandatory_evaluators", "room_plans",
        "room_plan_recruits", "room_plan_evaluators", "assignment_rounds", "assignments",
    ])
    tables = metadata.tables
    journeys = tables["journeys"]
    evaluators = tables["evaluators"]
    operations = tables["activity_operations"]
    availability = tables["activity_evaluator_availability"]
    activity_mandatory = tables["activity_mandatory_evaluators"]
    legacy_mandatory = tables["mandatory_room_evaluators"]
    room_plans = tables["room_plans"]
    room_recruits = tables["room_plan_recruits"]
    room_evaluators = tables["room_plan_evaluators"]
    rounds = tables["assignment_rounds"]
    assignments = tables["assignments"]
    now = datetime.now(timezone.utc)

    for journey in bind.execute(sa.select(journeys)).mappings():
        journey_id = journey["id"]
        evaluator_rows = list(bind.execute(sa.select(evaluators).where(
            evaluators.c.journey_id == journey_id, evaluators.c.active.is_(True))).mappings())
        legacy_required = list(bind.execute(sa.select(legacy_mandatory).where(
            legacy_mandatory.c.journey_id == journey_id)).mappings())
        max_version = bind.execute(sa.select(sa.func.max(room_plans.c.version)).where(
            room_plans.c.journey_id == journey_id)).scalar() or 0
        previous_code = None
        for code in ("sport", "escape_room", "negotiation", "skills"):
            existing_operation = bind.execute(sa.select(operations.c.id).where(
                operations.c.journey_id == journey_id, operations.c.activity_code == code)).scalar()
            if not existing_operation:
                bind.execute(operations.insert().values(
                    id=_id(), journey_id=journey_id, activity_code=code,
                    room_count=max(int(journey["room_count"] or 1), 1), initialized_from=previous_code,
                    version=1, created_at=now, updated_at=now,
                ))
            published_round = bind.execute(sa.select(rounds.c.id).where(
                rounds.c.journey_id == journey_id, rounds.c.activity_code == code,
                rounds.c.status == "published").order_by(rounds.c.version.desc())).scalar()
            assigned_evaluators = set(bind.execute(sa.select(assignments.c.evaluator_id).where(
                assignments.c.round_id == published_round)).scalars()) if published_round else set()
            existing_availability = set(bind.execute(sa.select(availability.c.evaluator_id).where(
                availability.c.journey_id == journey_id, availability.c.activity_code == code)).scalars())
            for evaluator in evaluator_rows:
                if evaluator["id"] not in existing_availability:
                    bind.execute(availability.insert().values(
                        id=_id(), journey_id=journey_id, activity_code=code, evaluator_id=evaluator["id"],
                        available=bool(evaluator["present"] or evaluator["id"] in assigned_evaluators),
                        version=1, updated_at=now,
                    ))
            if code == "sport":
                previous_code = code
                continue
            existing_required = set(bind.execute(sa.select(activity_mandatory.c.evaluator_id).where(
                activity_mandatory.c.journey_id == journey_id,
                activity_mandatory.c.activity_code == code)).scalars())
            for required in legacy_required:
                if required["evaluator_id"] not in existing_required:
                    bind.execute(activity_mandatory.insert().values(
                        id=_id(), journey_id=journey_id, activity_code=code,
                        evaluator_id=required["evaluator_id"], room_number=required["room_number"],
                    ))

            existing_plan = bind.execute(sa.select(room_plans.c.id).where(
                room_plans.c.journey_id == journey_id, room_plans.c.activity_code == code,
                room_plans.c.status == "published").order_by(room_plans.c.version.desc())).scalar()
            if existing_plan:
                if published_round:
                    bind.execute(rounds.update().where(rounds.c.id == published_round).values(room_plan_id=existing_plan))
                previous_code = code
                continue
            recruit_map: dict[str, int] = {}
            evaluator_map: dict[str, int] = {}
            if published_round:
                for row in bind.execute(sa.select(assignments).where(assignments.c.round_id == published_round)).mappings():
                    if row["room_number"] is not None:
                        recruit_map.setdefault(row["recruit_id"], row["room_number"])
                        evaluator_map.setdefault(row["evaluator_id"], row["room_number"])
            source_plan = bind.execute(sa.select(room_plans.c.id).where(
                room_plans.c.journey_id == journey_id, room_plans.c.status == "published"
            ).order_by(room_plans.c.version.desc())).scalar()
            if not recruit_map and source_plan:
                recruit_map = dict(bind.execute(sa.select(room_recruits.c.recruit_id, room_recruits.c.room_number).where(
                    room_recruits.c.plan_id == source_plan)).all())
            if not evaluator_map and source_plan:
                evaluator_map = dict(bind.execute(sa.select(room_evaluators.c.evaluator_id, room_evaluators.c.room_number).where(
                    room_evaluators.c.plan_id == source_plan)).all())
            if recruit_map or evaluator_map:
                max_version += 1
                plan_id = _id()
                bind.execute(room_plans.insert().values(
                    id=plan_id, journey_id=journey_id, activity_code=code, version=max_version,
                    edit_revision=1, status="published", seed="historical-migration", warnings_json="[]",
                    created_by="Migration 0016", created_at=now, published_at=now,
                ))
                for recruit_id, room_number in recruit_map.items():
                    bind.execute(room_recruits.insert().values(
                        id=_id(), plan_id=plan_id, recruit_id=recruit_id,
                        room_number=room_number, locked=True,
                    ))
                for evaluator_id, room_number in evaluator_map.items():
                    bind.execute(room_evaluators.insert().values(
                        id=_id(), plan_id=plan_id, evaluator_id=evaluator_id,
                        room_number=room_number, mandatory=False,
                    ))
                if published_round:
                    bind.execute(rounds.update().where(rounds.c.id == published_round).values(room_plan_id=plan_id))
            previous_code = code


def upgrade() -> None:
    bind = op.get_bind()
    recruit_columns = _columns(bind, "recruits")
    for name, type_ in (
        ("photo_object_key", sa.String(500)),
        ("photo_sha256", sa.String(64)),
        ("photo_size", sa.Integer()),
    ):
        if name not in recruit_columns:
            op.add_column("recruits", sa.Column(name, type_, nullable=True))
    length_fn = "length(photo_data)" if bind.dialect.name == "sqlite" else "octet_length(photo_data)"
    bind.execute(sa.text(
        f"update recruits set photo_size={length_fn} "
        "where photo_data is not null and photo_size is null"
    ))

    room_columns = _columns(bind, "room_plans")
    if "activity_code" not in room_columns:
        op.add_column("room_plans", sa.Column("activity_code", sa.String(30), nullable=False, server_default="escape_room"))
    if "edit_revision" not in room_columns:
        op.add_column("room_plans", sa.Column("edit_revision", sa.Integer(), nullable=False, server_default="1"))
    assignment_columns = _columns(bind, "assignment_rounds")
    if "edit_revision" not in assignment_columns:
        op.add_column("assignment_rounds", sa.Column("edit_revision", sa.Integer(), nullable=False, server_default="1"))
    if "room_plan_id" not in assignment_columns:
        op.add_column("assignment_rounds", sa.Column("room_plan_id", sa.String(36), nullable=True))
        if bind.dialect.name != "sqlite":
            op.create_foreign_key("fk_assignment_round_room_plan", "assignment_rounds", "room_plans", ["room_plan_id"], ["id"])
    assignment_item_columns = _columns(bind, "assignments")
    if "task_key" not in assignment_item_columns:
        op.add_column("assignments", sa.Column("task_key", sa.String(36), nullable=True))
        bind.execute(sa.text("update assignments set task_key=id where task_key is null"))
        op.create_index("ix_assignments_task_key", "assignments", ["task_key"], unique=False)
    member_columns = _columns(bind, "room_plan_recruits")
    if "locked" not in member_columns:
        op.add_column("room_plan_recruits", sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.false()))

    inspector = sa.inspect(bind)
    if not inspector.has_table("activity_operations"):
        op.create_table(
            "activity_operations",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("journey_id", sa.String(36), sa.ForeignKey("journeys.id", ondelete="CASCADE"), nullable=False),
            sa.Column("activity_code", sa.String(30), nullable=False),
            sa.Column("room_count", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("initialized_from", sa.String(30), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("journey_id", "activity_code", name="uq_activity_operation"),
        )
    if not inspector.has_table("activity_evaluator_availability"):
        op.create_table(
            "activity_evaluator_availability",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("journey_id", sa.String(36), sa.ForeignKey("journeys.id", ondelete="CASCADE"), nullable=False),
            sa.Column("activity_code", sa.String(30), nullable=False),
            sa.Column("evaluator_id", sa.String(36), sa.ForeignKey("evaluators.id", ondelete="CASCADE"), nullable=False),
            sa.Column("available", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("journey_id", "activity_code", "evaluator_id", name="uq_activity_evaluator_availability"),
        )
    if not inspector.has_table("activity_mandatory_evaluators"):
        op.create_table(
            "activity_mandatory_evaluators",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("journey_id", sa.String(36), sa.ForeignKey("journeys.id", ondelete="CASCADE"), nullable=False),
            sa.Column("activity_code", sa.String(30), nullable=False),
            sa.Column("evaluator_id", sa.String(36), sa.ForeignKey("evaluators.id", ondelete="CASCADE"), nullable=False),
            sa.Column("room_number", sa.Integer(), nullable=False),
            sa.UniqueConstraint("journey_id", "activity_code", "evaluator_id", name="uq_activity_mandatory_evaluator"),
        )

    # Historical room plans were shared by Escape and Negotiation. Keep the
    # original as Escape; activity plans are copied lazily when first opened.
    bind.execute(sa.text("update room_plans set activity_code='escape_room' where activity_code is null or activity_code=''"))
    _backfill_activity_plans(bind)

    # The established LRC workflow now runs Skills and Simulation in one shared
    # group plan. Do not alter neutral or owner-created workspaces.
    lrc_systems = list(bind.execute(sa.text(
        "select id, draft_json from assessment_systems where slug in "
        "('lrc-journee-recruitment-2026', 'lrc-journee-recruitment-2027-test')"
    )))
    for system_id, draft_json in lrc_systems:
        def convert(raw: str) -> str:
            value = json.loads(raw)
            for activity in value.get("activities", []):
                if activity.get("key") in {"skills", "simulation"}:
                    assignment = activity.setdefault("assignment", {})
                    assignment["mode"] = "automatic_groups"
                    assignment["mandatoryPlacements"] = True
            return json.dumps(value, separators=(",", ":"))
        bind.execute(sa.text("update assessment_systems set draft_json=:value where id=:id"),
                     {"value": convert(draft_json), "id": system_id})
        versions = list(bind.execute(sa.text(
            "select id, definition_json from assessment_system_versions where system_id=:id"
        ), {"id": system_id}))
        for version_id, definition_json in versions:
            bind.execute(sa.text("update assessment_system_versions set definition_json=:value where id=:id"),
                         {"value": convert(definition_json), "id": version_id})


def downgrade() -> None:
    op.drop_table("activity_mandatory_evaluators")
    op.drop_table("activity_evaluator_availability")
    op.drop_table("activity_operations")
    op.drop_column("room_plan_recruits", "locked")
    op.drop_column("room_plans", "activity_code")
    op.drop_column("room_plans", "edit_revision")
    op.drop_column("assignment_rounds", "edit_revision")
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint("fk_assignment_round_room_plan", "assignment_rounds", type_="foreignkey")
    op.drop_column("assignment_rounds", "room_plan_id")
    op.drop_index("ix_assignments_task_key", table_name="assignments")
    op.drop_column("assignments", "task_key")
    op.drop_column("recruits", "photo_size")
    op.drop_column("recruits", "photo_sha256")
    op.drop_column("recruits", "photo_object_key")
