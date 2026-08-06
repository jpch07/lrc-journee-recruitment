from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Journey(Base):
    __tablename__ = "journeys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    public_token: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    room_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    current_activity: Mapped[str | None] = mapped_column(String(30), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ActivityState(Base):
    __tablename__ = "activity_states"
    __table_args__ = (UniqueConstraint("journey_id", "code", name="uq_activity_journey_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    journey_id: Mapped[str] = mapped_column(ForeignKey("journeys.id", ondelete="CASCADE"), nullable=False)
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="not_started")
    assignment_round_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RubricSnapshot(Base):
    __tablename__ = "rubric_snapshots"
    __table_args__ = (UniqueConstraint("journey_id", "version", name="uq_rubric_snapshot_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    journey_id: Mapped[str] = mapped_column(ForeignKey("journeys.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False, default="2026")
    rubric_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Recruit(Base):
    __tablename__ = "recruits"
    __table_args__ = (
        Index("ix_recruits_journey_active", "journey_id", "active"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    journey_id: Mapped[str] = mapped_column(ForeignKey("journeys.id", ondelete="CASCADE"), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    phone_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    present: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    arrival_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    photo_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    photo_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    photo_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class EvaluatorDirectory(Base):
    __tablename__ = "evaluator_directory"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    default_role: Mapped[str] = mapped_column(String(20), nullable=False, default="dossard")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Evaluator(Base):
    __tablename__ = "evaluators"
    __table_args__ = (Index("ix_evaluators_journey_active", "journey_id", "active"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    journey_id: Mapped[str] = mapped_column(ForeignKey("journeys.id", ondelete="CASCADE"), nullable=False)
    directory_id: Mapped[str | None] = mapped_column(ForeignKey("evaluator_directory.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="dossard")
    present: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class MandatoryRoomEvaluator(Base):
    __tablename__ = "mandatory_room_evaluators"
    __table_args__ = (
        UniqueConstraint("journey_id", "evaluator_id", name="uq_mandatory_evaluator_once"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    journey_id: Mapped[str] = mapped_column(ForeignKey("journeys.id", ondelete="CASCADE"), nullable=False)
    evaluator_id: Mapped[str] = mapped_column(ForeignKey("evaluators.id", ondelete="CASCADE"), nullable=False)
    room_number: Mapped[int] = mapped_column(Integer, nullable=False)


class RoomPlan(Base):
    __tablename__ = "room_plans"
    __table_args__ = (UniqueConstraint("journey_id", "version", name="uq_room_plan_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    journey_id: Mapped[str] = mapped_column(ForeignKey("journeys.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="preview")
    seed: Mapped[str] = mapped_column(String(100), nullable=False)
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RoomPlanRecruit(Base):
    __tablename__ = "room_plan_recruits"
    __table_args__ = (
        UniqueConstraint("plan_id", "recruit_id", name="uq_room_plan_recruit"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    plan_id: Mapped[str] = mapped_column(ForeignKey("room_plans.id", ondelete="CASCADE"), nullable=False)
    room_number: Mapped[int] = mapped_column(Integer, nullable=False)
    recruit_id: Mapped[str] = mapped_column(ForeignKey("recruits.id", ondelete="CASCADE"), nullable=False)


class RoomPlanEvaluator(Base):
    __tablename__ = "room_plan_evaluators"
    __table_args__ = (
        UniqueConstraint("plan_id", "evaluator_id", name="uq_room_plan_evaluator"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    plan_id: Mapped[str] = mapped_column(ForeignKey("room_plans.id", ondelete="CASCADE"), nullable=False)
    room_number: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluator_id: Mapped[str] = mapped_column(ForeignKey("evaluators.id", ondelete="CASCADE"), nullable=False)
    mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class AssignmentRound(Base):
    __tablename__ = "assignment_rounds"
    __table_args__ = (
        UniqueConstraint("journey_id", "activity_code", "version", name="uq_assignment_round_version"),
        Index("ix_assignment_round_current", "journey_id", "activity_code", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    journey_id: Mapped[str] = mapped_column(ForeignKey("journeys.id", ondelete="CASCADE"), nullable=False)
    activity_code: Mapped[str] = mapped_column(String(30), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="preview")
    seed: Mapped[str] = mapped_column(String(100), nullable=False)
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    reused_from_id: Mapped[str | None] = mapped_column(ForeignKey("assignment_rounds.id"), nullable=True)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (
        UniqueConstraint("round_id", "evaluator_id", "recruit_id", name="uq_round_pair"),
        Index("ix_assignments_evaluator", "round_id", "evaluator_id"),
        Index("ix_assignments_recruit", "round_id", "recruit_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    round_id: Mapped[str] = mapped_column(ForeignKey("assignment_rounds.id", ondelete="CASCADE"), nullable=False)
    evaluator_id: Mapped[str] = mapped_column(ForeignKey("evaluators.id", ondelete="CASCADE"), nullable=False)
    recruit_id: Mapped[str] = mapped_column(ForeignKey("recruits.id", ondelete="CASCADE"), nullable=False)
    room_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    slot: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    repeated_pair: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    repeat_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)


class EvaluationSubmission(Base):
    __tablename__ = "evaluation_submissions"
    __table_args__ = (
        UniqueConstraint("assignment_id", name="uq_submission_assignment"),
        Index("ix_submission_recruit_activity", "recruit_id", "activity_code"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    assignment_id: Mapped[str] = mapped_column(ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False)
    journey_id: Mapped[str] = mapped_column(ForeignKey("journeys.id", ondelete="CASCADE"), nullable=False)
    activity_code: Mapped[str] = mapped_column(String(30), nullable=False)
    evaluator_id: Mapped[str] = mapped_column(ForeignKey("evaluators.id"), nullable=False)
    recruit_id: Mapped[str] = mapped_column(ForeignKey("recruits.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    responses_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    raw_payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    comments: Mapped[str] = mapped_column(Text, nullable=False, default="")
    score: Mapped[Decimal] = mapped_column(Numeric(8, 5), nullable=False, default=Decimal("0"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SubmissionVersion(Base):
    __tablename__ = "submission_versions"
    __table_args__ = (UniqueConstraint("submission_id", "version", name="uq_submission_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    submission_id: Mapped[str] = mapped_column(ForeignKey("evaluation_submissions.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(8, 5), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_name: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GeneralAssessment(Base):
    __tablename__ = "general_assessments"

    recruit_id: Mapped[str] = mapped_column(ForeignKey("recruits.id", ondelete="CASCADE"), primary_key=True)
    punctuality: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    respect: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    seriousness: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AdminSession(Base):
    __tablename__ = "admin_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor_name: Mapped[str] = mapped_column(String(200), nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(80), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EvaluatorSession(Base):
    __tablename__ = "evaluator_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    journey_id: Mapped[str] = mapped_column(ForeignKey("journeys.id", ondelete="CASCADE"), nullable=False)
    evaluator_id: Mapped[str] = mapped_column(ForeignKey("evaluators.id", ondelete="CASCADE"), nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(80), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RecruitAttendanceAccess(Base):
    __tablename__ = "recruit_attendance_access"

    journey_id: Mapped[str] = mapped_column(ForeignKey("journeys.id", ondelete="CASCADE"), primary_key=True)
    token: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RecruitAttendanceSession(Base):
    __tablename__ = "recruit_attendance_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    journey_id: Mapped[str] = mapped_column(ForeignKey("journeys.id", ondelete="CASCADE"), nullable=False)
    actor_name: Mapped[str] = mapped_column(String(200), nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(80), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EventDayProtection(Base):
    __tablename__ = "event_day_protections"
    __table_args__ = (UniqueConstraint("journey_id", name="uq_event_day_protection_journey"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    journey_id: Mapped[str] = mapped_column(ForeignKey("journeys.id", ondelete="CASCADE"), nullable=False)
    activation_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    duration_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="starting")
    github_run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    github_run_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("scope", "request_key", name="uq_idempotency_scope_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scope: Mapped[str] = mapped_column(String(200), nullable=False)
    request_key: Mapped[str] = mapped_column(String(120), nullable=False)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_journey_created", "journey_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    journey_id: Mapped[str | None] = mapped_column(ForeignKey("journeys.id", ondelete="CASCADE"), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_name: Mapped[str] = mapped_column(String(200), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    before_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    after_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
