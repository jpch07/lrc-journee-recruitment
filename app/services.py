from __future__ import annotations

import io
import hashlib
import random
import secrets
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from PIL import Image, ImageOps
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from .assignments import (
    EvaluatorCandidate,
    Pairing,
    PairingResult,
    RecruitCandidate,
    distribute_evaluators_to_rooms,
    generate_pairings,
    generate_room_plan,
)
from .models import (
    ActivityEvaluatorAvailability,
    ActivityMandatoryEvaluator,
    ActivityOperation,
    ActivityState,
    AdminEvaluation,
    Assignment,
    AssignmentRound,
    EvaluationSubmission,
    Evaluator,
    EvaluatorDirectory,
    GeneralAssessment,
    Journey,
    MandatoryRoomEvaluator,
    Recruit,
    RecruitAttendanceAccess,
    RoomPlan,
    RoomPlanEvaluator,
    RoomPlanRecruit,
    RubricSnapshot,
    SubmissionVersion,
    new_id,
    utcnow,
)
from .rubric import (
    ACTIVITY_ORDER,
    BEHAVIORAL_DIMENSIONS,
    DIMENSION_ACTIVITIES,
    DIMENSION_NAMES,
    DIMENSION_ORDER,
    ROOM_ACTIVITIES,
    RUBRICS,
    public_rubric,
)
from .scoring import (
    ScoringError,
    color_grade,
    configured_ranks,
    general_average,
    overall_score,
    target_activity_score,
    weighted_activity_score,
)
from .utils import audit, dumps, loads
from .object_storage import has_photo
from .assessment_runtime import (
    active_assessment_definition,
    activity_definition,
    primary_category_order,
    secondary_category_order,
)


def get_journey_or_404(db: Session, journey_id: str) -> Journey:
    journey = db.get(Journey, journey_id)
    if not journey:
        raise HTTPException(status_code=404, detail="Journee not found.")
    return journey


def operation_activity_code(activity_code: str) -> str:
    configured = activity_definition(activity_code)
    return configured.assignment.reuseAssignmentsFrom if configured and configured.assignment.reuseAssignmentsFrom else activity_code


def preceding_operation_activity(activity_code: str) -> str:
    """Return the activity whose availability should seed this operation.

    Room inheritance remains explicitly configured through ``predecessor``. The
    availability chain follows every enabled activity, so a room activity can
    inherit earlier attendance without attempting to copy a non-room plan.
    """
    code = operation_activity_code(activity_code)
    configured = activity_definition(code)
    if configured and configured.assignment.predecessor:
        return operation_activity_code(configured.assignment.predecessor)
    enabled = [item.key for item in active_assessment_definition().activities if item.enabled]
    try:
        index = enabled.index(code)
    except ValueError:
        return ""
    for previous in reversed(enabled[:index]):
        previous_code = operation_activity_code(previous)
        if previous_code != code:
            return previous_code
    return ""


def ensure_activity_operation(db: Session, journey: Journey, activity_code: str) -> ActivityOperation:
    """Create an independent activity operation, copying only its initial availability."""
    code = operation_activity_code(activity_code)
    predecessor = preceding_operation_activity(code)
    operation = db.scalar(select(ActivityOperation).where(
        ActivityOperation.journey_id == journey.id,
        ActivityOperation.activity_code == code,
    ))
    if operation:
        if operation.initialized_from != (predecessor or None):
            operation.initialized_from = predecessor or None
        if predecessor:
            ensure_activity_operation(db, journey, predecessor)
        _sync_activity_availability(db, journey, code, predecessor)
        db.flush()
        return operation
    if predecessor:
        ensure_activity_operation(db, journey, predecessor)
    prior = db.scalar(select(ActivityOperation).where(
        ActivityOperation.journey_id == journey.id,
        ActivityOperation.activity_code == predecessor,
    )) if predecessor else None
    operation = ActivityOperation(
        journey_id=journey.id,
        activity_code=code,
        room_count=prior.room_count if prior else journey.room_count,
        initialized_from=predecessor or None,
    )
    db.add(operation)
    db.flush()
    _sync_activity_availability(db, journey, code, predecessor)
    db.flush()
    return operation


def _sync_activity_availability(db: Session, journey: Journey, code: str, predecessor: str) -> None:
    """Backfill activity records when evaluators are added after a plan was created."""
    source_values = {
        item.evaluator_id: item.available for item in db.scalars(select(ActivityEvaluatorAvailability).where(
            ActivityEvaluatorAvailability.journey_id == journey.id,
            ActivityEvaluatorAvailability.activity_code == predecessor,
        ))
    } if predecessor else {}
    existing = set(db.scalars(select(ActivityEvaluatorAvailability.evaluator_id).where(
        ActivityEvaluatorAvailability.journey_id == journey.id,
        ActivityEvaluatorAvailability.activity_code == code,
    )))
    evaluators = list(db.scalars(select(Evaluator).where(
        Evaluator.journey_id == journey.id, Evaluator.active.is_(True))))
    for evaluator in evaluators:
        if evaluator.id not in existing:
            db.add(ActivityEvaluatorAvailability(
                journey_id=journey.id,
                activity_code=code,
                evaluator_id=evaluator.id,
                available=source_values.get(evaluator.id, evaluator.present),
            ))


def get_recruit_or_404(db: Session, journey_id: str, recruit_id: str) -> Recruit:
    recruit = db.get(Recruit, recruit_id)
    if not recruit or recruit.journey_id != journey_id:
        raise HTTPException(status_code=404, detail="Recruit not found.")
    return recruit


def get_evaluator_or_404(db: Session, journey_id: str, evaluator_id: str) -> Evaluator:
    evaluator = db.get(Evaluator, evaluator_id)
    if not evaluator or evaluator.journey_id != journey_id:
        raise HTTPException(status_code=404, detail="Evaluator not found.")
    return evaluator


def populate_journey_evaluators_from_directory(db: Session, journey_id: str) -> list[Evaluator]:
    existing = list(db.scalars(select(Evaluator).where(Evaluator.journey_id == journey_id)))
    by_directory = {item.directory_id: item for item in existing if item.directory_id}
    by_name = {item.name.casefold(): item for item in existing}
    added: list[Evaluator] = []
    directory = list(
        db.scalars(
            select(EvaluatorDirectory)
            .where(EvaluatorDirectory.active.is_(True))
            .order_by(EvaluatorDirectory.name)
        )
    )
    for entry in directory:
        evaluator = by_directory.get(entry.id) or by_name.get(entry.name.casefold())
        if evaluator:
            if not evaluator.directory_id:
                evaluator.directory_id = entry.id
            continue
        evaluator = Evaluator(
            journey_id=journey_id,
            directory_id=entry.id,
            name=entry.name,
            role=entry.default_role,
            present=False,
            active=True,
        )
        db.add(evaluator)
        added.append(evaluator)
    return added


def create_journey(db: Session, name: str, event_date, room_count: int, actor_name: str) -> Journey:
    journey = Journey(
        name=" ".join(name.split()),
        event_date=event_date,
        room_count=room_count,
        public_token=secrets.token_urlsafe(32),
    )
    db.add(journey)
    db.flush()
    db.add(RecruitAttendanceAccess(journey_id=journey.id, token=secrets.token_urlsafe(32)))
    for code in ACTIVITY_ORDER:
        db.add(ActivityState(journey_id=journey.id, code=code))
    db.add(
        RubricSnapshot(
            journey_id=journey.id,
            version="2026",
            rubric_json=dumps([public_rubric(code) for code in ACTIVITY_ORDER]),
        )
    )
    populate_journey_evaluators_from_directory(db, journey.id)
    audit(
        db,
        journey_id=journey.id,
        actor_type="admin",
        actor_name=actor_name,
        action="journey.created",
        entity_type="journey",
        entity_id=journey.id,
        after={"name": journey.name, "eventDate": journey.event_date, "roomCount": room_count},
    )
    return journey


def serialize_journey(db: Session, journey: Journey, *, include_token: bool = False) -> dict:
    recruits = db.scalar(
        select(func.count()).select_from(Recruit).where(Recruit.journey_id == journey.id, Recruit.active.is_(True))
    ) or 0
    present_recruits = db.scalar(
        select(func.count()).select_from(Recruit).where(
            Recruit.journey_id == journey.id, Recruit.active.is_(True), Recruit.present.is_(True)
        )
    ) or 0
    evaluators = db.scalar(
        select(func.count()).select_from(Evaluator).where(Evaluator.journey_id == journey.id, Evaluator.active.is_(True))
    ) or 0
    present_evaluators = db.scalar(
        select(func.count()).select_from(Evaluator).where(
            Evaluator.journey_id == journey.id, Evaluator.active.is_(True), Evaluator.present.is_(True)
        )
    ) or 0
    result = {
        "id": journey.id,
        "name": journey.name,
        "eventDate": journey.event_date.isoformat(),
        "status": journey.status,
        "roomCount": journey.room_count,
        "currentActivity": journey.current_activity,
        "version": journey.version,
        "recruitCount": recruits,
        "presentRecruitCount": present_recruits,
        "evaluatorCount": evaluators,
        "presentEvaluatorCount": present_evaluators,
        "updatedAt": journey.updated_at.isoformat(),
    }
    if include_token:
        from .models import AssessmentSystem

        system_slug = db.scalar(select(AssessmentSystem.slug).where(AssessmentSystem.id == journey.system_id))
        workspace_root = f"/{system_slug}" if system_slug else ""
        result["publicToken"] = journey.public_token
        result["evaluatorPath"] = f"{workspace_root}/evaluate"
        attendance_access = db.get(RecruitAttendanceAccess, journey.id)
        result["recruitAttendancePath"] = (
            f"{workspace_root}/attendance/{attendance_access.token}" if attendance_access else None
        )
    return result


def serialize_recruit(recruit: Recruit) -> dict:
    return {
        "id": recruit.id,
        "directoryId": recruit.directory_id,
        "phoneNumber": recruit.phone_number or "",
        "dateOfBirth": recruit.date_of_birth.isoformat() if recruit.date_of_birth else None,
        "name": recruit.name,
        "present": recruit.present,
        "arrivalTime": recruit.arrival_time.isoformat() if recruit.arrival_time else None,
        "attendanceComment": recruit.attendance_comment or "",
        "active": recruit.active,
        "hasPhoto": has_photo(recruit),
        "version": recruit.version,
    }


def serialize_evaluator(evaluator: Evaluator) -> dict:
    return {
        "id": evaluator.id,
        "name": evaluator.name,
        "role": evaluator.role,
        "present": evaluator.present,
        "active": evaluator.active,
        "version": evaluator.version,
    }


def update_recruit_attendance_fields(
    db: Session,
    journey_id: str,
    recruit_id: str,
    base_version: int,
    changes: dict,
) -> tuple[dict, Recruit]:
    recruit = db.get(Recruit, recruit_id)
    if not recruit or recruit.journey_id != journey_id or not recruit.active:
        raise HTTPException(status_code=404, detail="Recruit not found.")
    before = serialize_recruit(recruit)
    values: dict = {}
    if "present" in changes:
        values["present"] = bool(changes["present"])
    if "arrival_time" in changes:
        values["arrival_time"] = changes["arrival_time"]
    if "phone_number" in changes:
        values["phone_number"] = (changes["phone_number"] or "").strip() or None
    if "date_of_birth" in changes:
        values["date_of_birth"] = changes["date_of_birth"]
    if "attendance_comment" in changes:
        values["attendance_comment"] = (changes["attendance_comment"] or "").strip()
    effective_present = values.get("present", recruit.present)
    if not effective_present:
        values["arrival_time"] = None
    elif values.get("arrival_time") is None and "arrival_time" in values:
        raise HTTPException(status_code=422, detail="A present recruit must have a time of arrival.")
    values["version"] = Recruit.version + 1
    values["updated_at"] = utcnow()
    result = db.execute(
        update(Recruit)
        .where(
            Recruit.id == recruit_id,
            Recruit.journey_id == journey_id,
            Recruit.active.is_(True),
            Recruit.version == base_version,
        )
        .values(**values)
    )
    if result.rowcount != 1:
        db.expire_all()
        current = db.get(Recruit, recruit_id)
        if not current or current.journey_id != journey_id or not current.active:
            raise HTTPException(status_code=404, detail="Recruit not found.")
        raise HTTPException(
            status_code=409,
            detail=f"{current.name} was changed on another device. Retrying with the latest version is required.",
        )
    db.expire_all()
    return before, get_recruit_or_404(db, journey_id, recruit_id)


def process_photo(data: bytes) -> tuple[bytes, str]:
    if len(data) > 12 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Photo is too large; use a file below 12 MB.")
    try:
        with Image.open(io.BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((512, 512), Image.Resampling.LANCZOS)
            quality = 84
            while True:
                output = io.BytesIO()
                image.save(output, format="WEBP", quality=quality, method=6)
                result = output.getvalue()
                if len(result) <= 256 * 1024 or quality <= 45:
                    return result, "image/webp"
                quality -= 8
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail="The uploaded file is not a valid image.") from exc


def latest_room_plan(db: Session, journey_id: str, status: str, activity_code: str | None = None) -> RoomPlan | None:
    code = operation_activity_code(activity_code) if activity_code else None
    return db.scalar(
        select(RoomPlan)
        .where(
            RoomPlan.journey_id == journey_id,
            RoomPlan.status == status,
            *([RoomPlan.activity_code == code] if code else []),
        )
        .order_by(RoomPlan.version.desc())
    )


def room_plan_payload(db: Session, plan: RoomPlan) -> dict:
    recruits = {item.id: item for item in db.scalars(select(Recruit).where(Recruit.journey_id == plan.journey_id))}
    evaluators = {item.id: item for item in db.scalars(select(Evaluator).where(Evaluator.journey_id == plan.journey_id))}
    recruit_members = list(db.scalars(select(RoomPlanRecruit).where(RoomPlanRecruit.plan_id == plan.id)))
    evaluator_members = list(db.scalars(select(RoomPlanEvaluator).where(RoomPlanEvaluator.plan_id == plan.id)))
    operation = db.scalar(select(ActivityOperation).where(
        ActivityOperation.journey_id == plan.journey_id,
        ActivityOperation.activity_code == plan.activity_code,
    ))
    room_count = operation.room_count if operation else get_journey_or_404(db, plan.journey_id).room_count
    rooms: dict[int, dict] = {
        number: {"number": number, "recruits": [], "evaluators": []}
        for number in range(1, room_count + 1)
    }
    for member in recruit_members:
        recruit = recruits.get(member.recruit_id)
        if recruit:
            value = serialize_recruit(recruit)
            value["locked"] = member.locked
            rooms.setdefault(member.room_number, {"number": member.room_number, "recruits": [], "evaluators": []})[
                "recruits"
            ].append(value)
    for member in evaluator_members:
        evaluator = evaluators.get(member.evaluator_id)
        if evaluator:
            value = serialize_evaluator(evaluator)
            value["mandatory"] = member.mandatory
            value["locked"] = member.locked
            rooms.setdefault(member.room_number, {"number": member.room_number, "recruits": [], "evaluators": []})[
                "evaluators"
            ].append(value)
    return {
        "id": plan.id,
        "activityCode": plan.activity_code,
        "version": plan.version,
        "editRevision": plan.edit_revision,
        "status": plan.status,
        "seed": plan.seed,
        "warnings": loads(plan.warnings_json, []),
        "rooms": list(rooms.values()),
        "createdAt": plan.created_at.isoformat(),
        "publishedAt": plan.published_at.isoformat() if plan.published_at else None,
    }


def create_room_preview(db: Session, journey: Journey, actor_name: str, seed: str, activity_code: str = "escape_room") -> RoomPlan:
    db.scalar(select(Journey).where(Journey.id == journey.id).with_for_update())
    code = operation_activity_code(activity_code)
    operation = ensure_activity_operation(db, journey, code)
    recruits = list(
        db.scalars(select(Recruit).where(Recruit.journey_id == journey.id, Recruit.active.is_(True), Recruit.present.is_(True)))
    )
    available_ids = set(db.scalars(select(ActivityEvaluatorAvailability.evaluator_id).where(
        ActivityEvaluatorAvailability.journey_id == journey.id,
        ActivityEvaluatorAvailability.activity_code == code,
        ActivityEvaluatorAvailability.available.is_(True),
    )))
    evaluators = list(db.scalars(select(Evaluator).where(
        Evaluator.journey_id == journey.id, Evaluator.active.is_(True), Evaluator.id.in_(available_ids)))) if available_ids else []
    if not recruits:
        raise HTTPException(status_code=409, detail="Confirm at least one present recruit first.")
    mandatory_records = list(
        db.scalars(select(ActivityMandatoryEvaluator).where(
            ActivityMandatoryEvaluator.journey_id == journey.id,
            ActivityMandatoryEvaluator.activity_code == code,
        ))
    )
    try:
        result = generate_room_plan(
            [RecruitCandidate(item.id, item.name) for item in recruits],
            [EvaluatorCandidate(item.id, item.name, item.role) for item in evaluators],
            {item.evaluator_id: item.room_number for item in mandatory_records},
            operation.room_count,
            seed,
            primary_role_order=primary_category_order(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.execute(update(RoomPlan).where(
        RoomPlan.journey_id == journey.id,
        RoomPlan.activity_code == code,
        RoomPlan.status == "preview",
    ).values(status="superseded"))
    version = (db.scalar(select(func.max(RoomPlan.version)).where(RoomPlan.journey_id == journey.id)) or 0) + 1
    plan = RoomPlan(
        journey_id=journey.id,
        activity_code=code,
        version=version,
        status="preview",
        seed=seed,
        warnings_json=dumps(result.warnings),
        created_by=actor_name,
    )
    db.add(plan)
    db.flush()
    for recruit_id, room_number in result.recruit_rooms.items():
        db.add(RoomPlanRecruit(plan_id=plan.id, room_number=room_number, recruit_id=recruit_id))
    for evaluator_id, room_number in result.evaluator_rooms.items():
        db.add(
            RoomPlanEvaluator(
                plan_id=plan.id,
                room_number=room_number,
                evaluator_id=evaluator_id,
                mandatory=evaluator_id in result.mandatory_evaluators,
            )
        )
    audit(
        db,
        journey_id=journey.id,
        actor_type="admin",
        actor_name=actor_name,
        action="room_plan.previewed",
        entity_type="room_plan",
        entity_id=plan.id,
        after={"version": version, "warnings": result.warnings},
    )
    return plan


def activity_operation_payload(db: Session, journey: Journey, activity_code: str) -> dict:
    operation = ensure_activity_operation(db, journey, activity_code)
    code = operation.activity_code
    evaluators = {item.id: item for item in db.scalars(select(Evaluator).where(
        Evaluator.journey_id == journey.id, Evaluator.active.is_(True)))}
    directory = {item.id: item for item in db.scalars(select(EvaluatorDirectory).where(
        EvaluatorDirectory.id.in_([item.directory_id for item in evaluators.values() if item.directory_id])
    ))} if evaluators else {}
    availability = {item.evaluator_id: item for item in db.scalars(select(ActivityEvaluatorAvailability).where(
        ActivityEvaluatorAvailability.journey_id == journey.id,
        ActivityEvaluatorAvailability.activity_code == code,
    ))}
    mandatory = {item.evaluator_id: item.room_number for item in db.scalars(select(ActivityMandatoryEvaluator).where(
        ActivityMandatoryEvaluator.journey_id == journey.id,
        ActivityMandatoryEvaluator.activity_code == code,
    ))}
    return {
        "activityCode": code,
        "roomCount": operation.room_count,
        "version": operation.version,
        "initializedFrom": operation.initialized_from,
        "roomSource": (
            operation_activity_code(activity_definition(code).assignment.predecessor)
            if activity_definition(code) and activity_definition(code).assignment.predecessor else None
        ),
        "evaluators": [{
            **serialize_evaluator(item),
            "fullName": directory[item.directory_id].full_name if item.directory_id in directory else "",
            "available": availability.get(item.id).available if item.id in availability else False,
            "availabilityVersion": availability.get(item.id).version if item.id in availability else 1,
            "mandatoryRoom": mandatory.get(item.id),
        } for item in sorted(evaluators.values(), key=lambda row: (
            not (availability.get(row.id).available if row.id in availability else False),
            row.role != "overall", row.name.casefold(),
        ))],
    }


def create_activity_room_preview(
    db: Session,
    journey: Journey,
    activity_code: str,
    actor_name: str,
    seed: str,
    mode: str,
) -> RoomPlan:
    """Create a working room plan while preserving the requested half of the plan."""
    code = operation_activity_code(activity_code)
    if mode == "full":
        operation = ensure_activity_operation(db, journey, code)
        current = latest_room_plan(db, journey.id, "preview", code) or latest_room_plan(db, journey.id, "published", code)
        configured = activity_definition(code)
        predecessor = operation_activity_code(configured.assignment.predecessor) if configured and configured.assignment.predecessor else ""
        source = ((latest_room_plan(db, journey.id, "published", predecessor)
                   or latest_room_plan(db, journey.id, "preview", predecessor)) if predecessor else None)
        if current or not source:
            return create_room_preview(db, journey, actor_name, seed, code)
        mode = "copy_recruits"
    operation = ensure_activity_operation(db, journey, code)
    current = latest_room_plan(db, journey.id, "preview", code) or latest_room_plan(db, journey.id, "published", code)
    recruit_ids = set(db.scalars(select(Recruit.id).where(
        Recruit.journey_id == journey.id, Recruit.active.is_(True), Recruit.present.is_(True))))
    evaluator_rows = list(db.scalars(select(Evaluator).where(Evaluator.journey_id == journey.id, Evaluator.active.is_(True))))
    evaluator_by_id = {item.id: item for item in evaluator_rows}
    available_ids = set(db.scalars(select(ActivityEvaluatorAvailability.evaluator_id).where(
        ActivityEvaluatorAvailability.journey_id == journey.id,
        ActivityEvaluatorAvailability.activity_code == code,
        ActivityEvaluatorAvailability.available.is_(True),
    )))
    evaluators = [EvaluatorCandidate(item.id, item.name, item.role) for item in evaluator_rows if item.id in available_ids]
    mandatory = {item.evaluator_id: item.room_number for item in db.scalars(select(ActivityMandatoryEvaluator).where(
        ActivityMandatoryEvaluator.journey_id == journey.id,
        ActivityMandatoryEvaluator.activity_code == code,
    ))}
    locked: set[str] = set()
    locked_evaluators: dict[str, int] = {}
    recruit_rooms: dict[str, int] = {}
    evaluator_rooms: dict[str, int] = {}
    if current:
        for item in db.scalars(select(RoomPlanRecruit).where(RoomPlanRecruit.plan_id == current.id)):
            if item.recruit_id in recruit_ids:
                recruit_rooms[item.recruit_id] = item.room_number
                if item.locked:
                    locked.add(item.recruit_id)
        for item in db.scalars(select(RoomPlanEvaluator).where(RoomPlanEvaluator.plan_id == current.id)):
            if item.evaluator_id in available_ids:
                evaluator_rooms[item.evaluator_id] = item.room_number
                if item.locked and not item.mandatory:
                    locked_evaluators[item.evaluator_id] = item.room_number
    if mode == "copy_recruits":
        configured = activity_definition(code)
        predecessor = operation_activity_code(configured.assignment.predecessor) if configured and configured.assignment.predecessor else ""
        source = latest_room_plan(db, journey.id, "published", predecessor) or latest_room_plan(db, journey.id, "preview", predecessor)
        if not source:
            raise HTTPException(status_code=409, detail="The preceding activity has no room plan to copy.")
        recruit_rooms = {item.recruit_id: item.room_number for item in db.scalars(
            select(RoomPlanRecruit).where(RoomPlanRecruit.plan_id == source.id)) if item.recruit_id in recruit_ids}
        locked = set(recruit_rooms)
    if mode == "copy_full":
        configured = activity_definition(code)
        predecessor = operation_activity_code(configured.assignment.predecessor) if configured and configured.assignment.predecessor else ""
        source = latest_room_plan(db, journey.id, "published", predecessor) or latest_room_plan(db, journey.id, "preview", predecessor)
        if not source:
            raise HTTPException(status_code=409, detail="The preceding activity has no room plan to copy.")
        source_operation = ensure_activity_operation(db, journey, predecessor)
        operation.room_count = source_operation.room_count
        operation.version += 1
        source_recruits = list(db.scalars(select(RoomPlanRecruit).where(RoomPlanRecruit.plan_id == source.id)))
        recruit_rooms = {item.recruit_id: item.room_number for item in source_recruits if item.recruit_id in recruit_ids}
        locked = {item.recruit_id for item in source_recruits if item.recruit_id in recruit_ids and item.locked}
        source_evaluators = list(db.scalars(select(RoomPlanEvaluator).where(RoomPlanEvaluator.plan_id == source.id)))
        evaluator_rooms = {item.evaluator_id: item.room_number for item in source_evaluators
                           if item.evaluator_id in available_ids}
        locked_evaluators = {item.evaluator_id: item.room_number for item in source_evaluators
                             if item.evaluator_id in available_ids and item.locked and not item.mandatory}
        source_mandatory = {item.evaluator_id: item.room_number for item in db.scalars(
            select(ActivityMandatoryEvaluator).where(
                ActivityMandatoryEvaluator.journey_id == journey.id,
                ActivityMandatoryEvaluator.activity_code == predecessor,
            )) if item.evaluator_id in available_ids}
        db.execute(delete(ActivityMandatoryEvaluator).where(
            ActivityMandatoryEvaluator.journey_id == journey.id,
            ActivityMandatoryEvaluator.activity_code == code,
        ))
        for evaluator_id, room_number in source_mandatory.items():
            db.add(ActivityMandatoryEvaluator(
                journey_id=journey.id, activity_code=code,
                evaluator_id=evaluator_id, room_number=room_number,
            ))
        mandatory = source_mandatory
    if mode == "recruits":
        randomizer = random.Random(int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big"))
        unassigned = sorted(recruit_ids - locked)
        randomizer.shuffle(unassigned)
        counts = Counter(recruit_rooms[item] for item in locked if item in recruit_rooms)
        for recruit_id in unassigned:
            room = min(range(1, operation.room_count + 1), key=lambda value: (counts[value], value))
            recruit_rooms[recruit_id] = room
            counts[room] += 1
    if not recruit_rooms or (set(recruit_rooms) != recruit_ids and mode != "copy_full"):
        raise HTTPException(status_code=409, detail="Place every present recruit or generate recruit rooms first.")
    warnings: list[str] = []
    mandatory_present: set[str] = set()
    if mode in {"evaluators", "copy_recruits"}:
        evaluator_rooms, mandatory_present, warnings = distribute_evaluators_to_rooms(
            recruit_rooms, evaluators, mandatory, operation.room_count, seed, primary_category_order(),
            locked_rooms=locked_evaluators if mode == "evaluators" else None)
    elif mode == "copy_full":
        mandatory_present = set(mandatory)
        missing_from_source = available_ids - set(evaluator_rooms)
        warnings = ([f"{len(missing_from_source)} currently available evaluator(s) were not in the previous room plan and remain unassigned."]
                    if missing_from_source else [])
    else:
        evaluator_rooms = {key: value for key, value in evaluator_rooms.items() if key in available_ids}
        mandatory_present = {key for key, value in mandatory.items() if evaluator_rooms.get(key) == value}
    db.execute(update(RoomPlan).where(
        RoomPlan.journey_id == journey.id, RoomPlan.activity_code == code, RoomPlan.status == "preview",
    ).values(status="superseded"))
    version = (db.scalar(select(func.max(RoomPlan.version)).where(RoomPlan.journey_id == journey.id)) or 0) + 1
    plan = RoomPlan(journey_id=journey.id, activity_code=code, version=version, status="preview",
                    seed=seed, warnings_json=dumps(warnings), created_by=actor_name)
    db.add(plan); db.flush()
    for recruit_id, room in recruit_rooms.items():
        db.add(RoomPlanRecruit(plan_id=plan.id, recruit_id=recruit_id, room_number=room, locked=recruit_id in locked))
    for evaluator_id, room in evaluator_rooms.items():
        db.add(RoomPlanEvaluator(plan_id=plan.id, evaluator_id=evaluator_id, room_number=room,
                                 mandatory=evaluator_id in mandatory_present,
                                 locked=evaluator_id in locked_evaluators))
    audit(db, journey_id=journey.id, actor_type="admin", actor_name=actor_name,
          action=f"room_plan.{mode}_generated", entity_type="room_plan", entity_id=plan.id,
          after={"activity": code, "version": version})
    return plan


def publish_room_plan(
    db: Session,
    journey: Journey,
    plan: RoomPlan,
    actor_name: str,
    *,
    override_reason: str = "",
) -> None:
    db.flush()
    if plan.journey_id != journey.id or plan.status != "preview":
        raise HTTPException(status_code=409, detail="Only a preview for this Journee can be published.")
    db.execute(
        update(RoomPlan)
        .where(
            RoomPlan.journey_id == journey.id,
            RoomPlan.activity_code == plan.activity_code,
            RoomPlan.status == "published",
        )
        .values(status="superseded")
    )
    plan.status = "published"
    plan.published_at = utcnow()
    audit(
        db,
        journey_id=journey.id,
        actor_type="admin",
        actor_name=actor_name,
        action="room_plan.override_published" if override_reason else "room_plan.published",
        entity_type="room_plan",
        entity_id=plan.id,
        after={"version": plan.version},
        reason=override_reason,
    )


def _room_candidates(db: Session, journey: Journey, activity_code: str, plan: RoomPlan | None = None) -> tuple[list[RecruitCandidate], list[EvaluatorCandidate]]:
    code = operation_activity_code(activity_code)
    ensure_activity_operation(db, journey, code)
    plan = plan or latest_room_plan(db, journey.id, "published", code)
    if not plan:
        raise HTTPException(status_code=409, detail="Publish a room plan before generating this activity.")
    recruit_members = list(db.scalars(select(RoomPlanRecruit).where(RoomPlanRecruit.plan_id == plan.id)))
    evaluator_members = list(db.scalars(select(RoomPlanEvaluator).where(RoomPlanEvaluator.plan_id == plan.id)))
    recruits = {item.id: item for item in db.scalars(select(Recruit).where(Recruit.journey_id == journey.id))}
    evaluators = {item.id: item for item in db.scalars(select(Evaluator).where(Evaluator.journey_id == journey.id))}
    available_ids = set(db.scalars(select(ActivityEvaluatorAvailability.evaluator_id).where(
        ActivityEvaluatorAvailability.journey_id == journey.id,
        ActivityEvaluatorAvailability.activity_code == code,
        ActivityEvaluatorAvailability.available.is_(True),
    )))
    return (
        [
            RecruitCandidate(member.recruit_id, recruits[member.recruit_id].name, member.room_number)
            for member in recruit_members
            if member.recruit_id in recruits and recruits[member.recruit_id].active and recruits[member.recruit_id].present
        ],
        [
            EvaluatorCandidate(member.evaluator_id, evaluators[member.evaluator_id].name, evaluators[member.evaluator_id].role, member.room_number)
            for member in evaluator_members
            if member.evaluator_id in evaluators and evaluators[member.evaluator_id].active
            and member.evaluator_id in available_ids
        ],
    )


def _global_candidates(db: Session, journey: Journey, activity_code: str) -> tuple[list[RecruitCandidate], list[EvaluatorCandidate]]:
    code = operation_activity_code(activity_code)
    ensure_activity_operation(db, journey, code)
    available_ids = set(db.scalars(select(ActivityEvaluatorAvailability.evaluator_id).where(
        ActivityEvaluatorAvailability.journey_id == journey.id,
        ActivityEvaluatorAvailability.activity_code == code,
        ActivityEvaluatorAvailability.available.is_(True),
    )))
    recruits = list(
        db.scalars(select(Recruit).where(Recruit.journey_id == journey.id, Recruit.active.is_(True), Recruit.present.is_(True)))
    )
    evaluators = list(
        db.scalars(select(Evaluator).where(
            Evaluator.journey_id == journey.id,
            Evaluator.active.is_(True),
            Evaluator.id.in_(available_ids),
        ))
    )
    return (
        [RecruitCandidate(item.id, item.name) for item in recruits],
        [EvaluatorCandidate(item.id, item.name, item.role) for item in evaluators],
    )


def past_pair_data(db: Session, journey_id: str, current_activity: str) -> tuple[set[tuple[str, str]], dict[str, int]]:
    definition = active_assessment_definition()
    independent = {
        item.key for item in definition.activities
        if item.enabled and not item.assignment.reuseAssignmentsFrom
    }
    independent.discard(current_activity)
    rows = db.execute(
        select(Assignment.evaluator_id, Assignment.recruit_id, Assignment.slot)
        .join(AssignmentRound, AssignmentRound.id == Assignment.round_id)
        .where(
            AssignmentRound.journey_id == journey_id,
            AssignmentRound.status == "published",
            AssignmentRound.activity_code.in_(independent),
        )
    ).all()
    pairs = {(row.evaluator_id, row.recruit_id) for row in rows}
    secondary_counts = Counter(row.recruit_id for row in rows if row.slot == 2)
    return pairs, dict(secondary_counts)


def create_assignment_preview(
    db: Session,
    journey: Journey,
    activity_code: str,
    actor_name: str,
    seed: str,
) -> AssignmentRound:
    db.scalar(select(Journey).where(Journey.id == journey.id).with_for_update())
    if activity_code not in RUBRICS:
        raise HTTPException(status_code=404, detail="Unknown activity.")
    configured_activity = activity_definition(activity_code)
    if configured_activity and configured_activity.assignment.reuseAssignmentsFrom:
        raise HTTPException(
            status_code=409,
            detail=(f"{configured_activity.name} reuses assignments from "
                    f"{RUBRICS[configured_activity.assignment.reuseAssignmentsFrom].name}. Generate them there."),
        )
    published = db.scalar(select(AssignmentRound).where(
        AssignmentRound.journey_id == journey.id,
        AssignmentRound.activity_code == activity_code,
        AssignmentRound.status == "published",
    ))
    published_task_keys = {
        (item.evaluator_id, item.recruit_id): item.task_key or item.id
        for item in db.scalars(select(Assignment).where(Assignment.round_id == published.id))
    } if published else {}
    version = (
        db.scalar(
            select(func.max(AssignmentRound.version)).where(
                AssignmentRound.journey_id == journey.id, AssignmentRound.activity_code == activity_code
            )
        )
        or 0
    ) + 1
    selected_room_plan = ((latest_room_plan(db, journey.id, "preview", activity_code)
                           or latest_room_plan(db, journey.id, "published", activity_code))
                          if activity_code in ROOM_ACTIVITIES else None)
    round_record = AssignmentRound(
        journey_id=journey.id,
        activity_code=activity_code,
        version=version,
        status="preview",
        seed=seed,
        created_by=actor_name,
        room_plan_id=selected_room_plan.id if selected_room_plan else None,
    )
    db.add(round_record)
    db.flush()
    db.execute(update(AssignmentRound).where(
        AssignmentRound.journey_id == journey.id,
        AssignmentRound.activity_code == activity_code,
        AssignmentRound.status == "preview",
        AssignmentRound.id != round_record.id,
    ).values(status="superseded"))

    recruits, evaluators = (
        _room_candidates(db, journey, activity_code, selected_room_plan) if activity_code in ROOM_ACTIVITIES else _global_candidates(db, journey, activity_code)
    )
    if not recruits:
        raise HTTPException(status_code=409, detail="No confirmed-present recruits are available.")
    past_pairs, secondary_counts = past_pair_data(db, journey.id, activity_code)
    if configured_activity and not configured_activity.assignment.avoidRepeatPairs:
        past_pairs = set()
    result = (generate_pairings(
        recruits,
        evaluators,
        room_based=activity_code in ROOM_ACTIVITIES,
        past_pairs=past_pairs,
        prior_secondary_counts=secondary_counts,
        seed=seed,
        primary_role_order=primary_category_order(),
        secondary_role_order=secondary_category_order(),
        maximum_assessors=(configured_activity.assignment.maximumAssessors if configured_activity else 2),
    ) if not configured_activity or configured_activity.assignment.mode != "manual"
              else PairingResult([], ["Manual assignment: add one primary assessor for every participant before publishing."]))
    round_record.warnings_json = dumps(result.warnings)
    for item in result.assignments:
        db.add(
            Assignment(
                round_id=round_record.id,
                evaluator_id=item.evaluator_id,
                recruit_id=item.recruit_id,
                room_number=item.room_number,
                slot=item.slot,
                repeated_pair=item.repeated_pair,
                repeat_reason=item.repeat_reason,
                task_key=published_task_keys.get((item.evaluator_id, item.recruit_id)) or new_id(),
            )
        )
    audit(
        db,
        journey_id=journey.id,
        actor_type="admin",
        actor_name=actor_name,
        action="assignments.previewed",
        entity_type="assignment_round",
        entity_id=round_record.id,
        after={"activity": activity_code, "version": version, "seed": seed},
    )
    return round_record


def assignment_round_payload(db: Session, round_record: AssignmentRound) -> dict:
    assignments = list(db.scalars(select(Assignment).where(Assignment.round_id == round_record.id)))
    recruits = {item.id: item for item in db.scalars(select(Recruit).where(Recruit.journey_id == round_record.journey_id))}
    evaluators = {item.id: item for item in db.scalars(select(Evaluator).where(Evaluator.journey_id == round_record.journey_id))}
    warnings = [
        message
        for message in loads(round_record.warnings_json, [])
        if message not in {
            "Shared Skills & Simulation assignment.",
            "Simulation reuses the published Skills assignment.",
        }
    ]
    return {
        "id": round_record.id,
        "activityCode": round_record.activity_code,
        "activityName": RUBRICS[round_record.activity_code].name,
        "version": round_record.version,
        "editRevision": round_record.edit_revision,
        "status": round_record.status,
        "seed": round_record.seed,
        "warnings": warnings,
        "reusedFromId": round_record.reused_from_id,
        "roomPlanId": round_record.room_plan_id,
        "assignments": [
            {
                "id": item.id,
                "evaluatorId": item.evaluator_id,
                "evaluatorName": evaluators[item.evaluator_id].name if item.evaluator_id in evaluators else "Unknown",
                "evaluatorRole": evaluators[item.evaluator_id].role if item.evaluator_id in evaluators else "",
                "recruitId": item.recruit_id,
                "recruitName": recruits[item.recruit_id].name if item.recruit_id in recruits else "Unknown",
                "roomNumber": item.room_number,
                "slot": item.slot,
                "repeatedPair": item.repeated_pair,
                "repeatReason": item.repeat_reason,
            }
            for item in sorted(assignments, key=lambda row: (row.room_number or 0, row.recruit_id, row.slot))
        ],
        "createdAt": round_record.created_at.isoformat(),
        "publishedAt": round_record.published_at.isoformat() if round_record.published_at else None,
    }


def publish_assignment_round(db: Session, journey: Journey, round_record: AssignmentRound, actor_name: str) -> None:
    if round_record.journey_id != journey.id or round_record.status != "preview":
        raise HTTPException(status_code=409, detail="Only a preview for this Journee can be published.")
    configured_activity = activity_definition(round_record.activity_code)
    if configured_activity and configured_activity.assignment.reuseAssignmentsFrom:
        raise HTTPException(
            status_code=409,
            detail=f"{configured_activity.name} assignments are published from its configured source activity.",
        )
    assignments = list(db.scalars(select(Assignment).where(Assignment.round_id == round_record.id)))
    state = db.scalar(
        select(ActivityState).where(
            ActivityState.journey_id == journey.id, ActivityState.code == round_record.activity_code
        )
    )
    if not state:
        raise HTTPException(status_code=500, detail="Activity state is missing.")
    definition = active_assessment_definition()
    reuse_targets = [item for item in definition.activities if item.enabled and
                     item.assignment.reuseAssignmentsFrom == round_record.activity_code]
    reuse_states = {item.key: db.scalar(select(ActivityState).where(
        ActivityState.journey_id == journey.id, ActivityState.code == item.key,
    )) for item in reuse_targets}
    if any(state is None for state in reuse_states.values()):
        raise HTTPException(status_code=500, detail="A configured shared-assignment activity state is missing.")
    previous = db.scalar(select(AssignmentRound).where(
        AssignmentRound.journey_id == journey.id,
        AssignmentRound.activity_code == round_record.activity_code,
        AssignmentRound.status == "published",
    ))
    if previous:
        previous_assignments = list(db.scalars(select(Assignment).where(Assignment.round_id == previous.id)))
        new_by_pair = {(item.evaluator_id, item.recruit_id): item for item in assignments}
        submitted = list(db.execute(
            select(EvaluationSubmission, Assignment, Evaluator, Recruit)
            .join(Assignment, Assignment.id == EvaluationSubmission.assignment_id)
            .join(Evaluator, Evaluator.id == Assignment.evaluator_id)
            .join(Recruit, Recruit.id == Assignment.recruit_id)
            .where(Assignment.round_id == previous.id)
        ))
        blockers = [
            f"{RUBRICS[round_record.activity_code].name}: {evaluator.name} -> {recruit.name} "
            f"(submitted {submission.submitted_at.isoformat() if submission.submitted_at else 'time unavailable'})"
            for submission, old_assignment, evaluator, recruit in submitted
            if submission.status in {"submitted", "locked"}
            and (old_assignment.evaluator_id, old_assignment.recruit_id) not in new_by_pair
        ]
        if blockers:
            raise HTTPException(
                status_code=409,
                detail="Cannot remove submitted assignment(s): " + "; ".join(blockers),
            )
        # Preserve the submission by moving it to the unchanged logical pair in
        # the newly published version.
        for submission, old_assignment, _evaluator, _recruit in submitted:
            replacement = new_by_pair.get((old_assignment.evaluator_id, old_assignment.recruit_id))
            if replacement:
                submission.assignment_id = replacement.id
    source_pairs = {(item.evaluator_id, item.recruit_id) for item in assignments}
    target_submissions: dict[str, list[tuple[EvaluationSubmission, Assignment, str, str]]] = {}
    target_task_keys: dict[str, dict[tuple[str, str], str]] = {}
    for target_activity in reuse_targets:
        previous_target = db.scalar(select(AssignmentRound).where(
            AssignmentRound.journey_id == journey.id,
            AssignmentRound.activity_code == target_activity.key,
            AssignmentRound.status == "published",
        ))
        if not previous_target:
            target_submissions[target_activity.key] = []
            target_task_keys[target_activity.key] = {}
            continue
        target_task_keys[target_activity.key] = {
            (item.evaluator_id, item.recruit_id): item.task_key or item.id
            for item in db.scalars(select(Assignment).where(Assignment.round_id == previous_target.id))
        }
        rows = list(db.execute(
            select(EvaluationSubmission, Assignment, Evaluator.name, Recruit.name)
            .join(Assignment, Assignment.id == EvaluationSubmission.assignment_id)
            .join(Evaluator, Evaluator.id == Assignment.evaluator_id)
            .join(Recruit, Recruit.id == Assignment.recruit_id)
            .where(Assignment.round_id == previous_target.id)
        ))
        blockers = [f"{target_activity.name}: {evaluator_name} -> {recruit_name} "
                    f"(submitted {submission.submitted_at.isoformat() if submission.submitted_at else 'time unavailable'})"
                    for submission, old, evaluator_name, recruit_name in rows
                    if submission.status in {"submitted", "locked"}
                    and (old.evaluator_id, old.recruit_id) not in source_pairs]
        if blockers:
            raise HTTPException(status_code=409, detail=(
                f"Cannot remove submitted {target_activity.name} assignment(s): " + "; ".join(blockers)
            ))
        target_submissions[target_activity.key] = rows
    db.execute(
        update(AssignmentRound)
        .where(
            AssignmentRound.journey_id == journey.id,
            AssignmentRound.activity_code == round_record.activity_code,
            AssignmentRound.status == "published",
        )
        .values(status="superseded")
    )
    round_record.status = "published"
    round_record.published_at = utcnow()
    state.assignment_round_id = round_record.id
    state.version += 1
    journey.version += 1
    audit(
        db,
        journey_id=journey.id,
        actor_type="admin",
        actor_name=actor_name,
        action="assignments.published",
        entity_type="assignment_round",
        entity_id=round_record.id,
        after={"activity": round_record.activity_code, "version": round_record.version, "count": len(assignments)},
    )
    for target_activity in reuse_targets:
        target_state = reuse_states[target_activity.key]
        # Evaluations remain distinct, but related activities receive an exact copy
        # of the one admin-managed pairing in the same transaction.
        target_version = (
            db.scalar(
                select(func.max(AssignmentRound.version)).where(
                    AssignmentRound.journey_id == journey.id,
                    AssignmentRound.activity_code == target_activity.key,
                )
            )
            or 0
        ) + 1
        db.execute(
            update(AssignmentRound)
            .where(
                AssignmentRound.journey_id == journey.id,
                AssignmentRound.activity_code == target_activity.key,
                AssignmentRound.status.in_(("preview", "published")),
            )
            .values(status="superseded")
        )
        target_round = AssignmentRound(
            journey_id=journey.id,
            activity_code=target_activity.key,
            version=target_version,
            status="published",
            seed=round_record.seed,
            warnings_json="[]",
            reused_from_id=round_record.id,
            room_plan_id=round_record.room_plan_id,
            created_by=actor_name,
            published_at=round_record.published_at,
        )
        db.add(target_round)
        db.flush()
        copied_by_pair: dict[tuple[str, str], Assignment] = {}
        for item in assignments:
            copied = Assignment(
                    round_id=target_round.id,
                    evaluator_id=item.evaluator_id,
                    recruit_id=item.recruit_id,
                    room_number=item.room_number,
                    slot=item.slot,
                    repeated_pair=False,
                    repeat_reason=None,
                    task_key=(target_task_keys[target_activity.key].get((item.evaluator_id, item.recruit_id))
                              or new_id()),
                )
            db.add(copied)
            db.flush()
            copied_by_pair[(item.evaluator_id, item.recruit_id)] = copied
        for submission, old, _evaluator_name, _recruit_name in target_submissions.get(target_activity.key, []):
            replacement = copied_by_pair.get((old.evaluator_id, old.recruit_id))
            if replacement:
                submission.assignment_id = replacement.id
        target_state.assignment_round_id = target_round.id
        target_state.version += 1
        audit(
            db,
            journey_id=journey.id,
            actor_type="admin",
            actor_name=actor_name,
            action="assignments.published_shared",
            entity_type="assignment_round",
            entity_id=target_round.id,
            after={
                "activities": [round_record.activity_code, target_activity.key],
                "sourceRoundId": round_record.id,
                "targetVersion": target_version,
                "count": len(assignments),
            },
        )


def result_snapshot(db: Session, journey: Journey) -> dict:
    definition = active_assessment_definition()
    configured_activities = [item for item in definition.activities if item.enabled]
    recruits = list(
        db.scalars(
            select(Recruit)
            .where(Recruit.journey_id == journey.id, Recruit.active.is_(True), Recruit.present.is_(True))
            .order_by(Recruit.name)
        )
    )
    states = {
        item.code: item
        for item in db.scalars(select(ActivityState).where(ActivityState.journey_id == journey.id))
    }
    round_by_id: dict[str, AssignmentRound] = {}
    round_ids: list[str] = []
    for state in states.values():
        if state.assignment_round_id:
            round_ids.append(state.assignment_round_id)
    if round_ids:
        round_by_id = {item.id: item for item in db.scalars(select(AssignmentRound).where(AssignmentRound.id.in_(round_ids)))}
    assignments = list(db.scalars(select(Assignment).where(Assignment.round_id.in_(round_ids)))) if round_ids else []
    assignment_activity = {
        item.id: round_by_id[item.round_id].activity_code
        for item in assignments
        if item.round_id in round_by_id
    }
    assignments_by_recruit_activity: dict[tuple[str, str], list[Assignment]] = defaultdict(list)
    for item in assignments:
        code = assignment_activity.get(item.id)
        if code:
            assignments_by_recruit_activity[(item.recruit_id, code)].append(item)
    assignment_ids = [item.id for item in assignments]
    submissions = (
        {item.assignment_id: item for item in db.scalars(select(EvaluationSubmission).where(EvaluationSubmission.assignment_id.in_(assignment_ids)))}
        if assignment_ids
        else {}
    )
    assessments = {
        item.recruit_id: item
        for item in db.scalars(
            select(GeneralAssessment).where(GeneralAssessment.recruit_id.in_([recruit.id for recruit in recruits]))
        )
    } if recruits else {}
    admin_evaluations = {
        (item.recruit_id, item.activity_code): item
        for item in db.scalars(
            select(AdminEvaluation).where(
                AdminEvaluation.journey_id == journey.id,
                AdminEvaluation.recruit_id.in_([recruit.id for recruit in recruits]),
            )
        )
    } if recruits else {}

    rows: list[dict] = []
    activity_rank_inputs: dict[str, list[tuple[str, Decimal]]] = {item.key: [] for item in configured_activities}
    dimension_rank_inputs: dict[str, list[tuple[str, Decimal]]] = {item.key: [] for item in definition.dimensions}
    overall_rank_inputs: list[tuple[str, Decimal]] = []
    for recruit in recruits:
        activity_values: dict[str, Decimal] = {}
        activities_payload: dict[str, dict] = {}
        submitted_by_activity: dict[str, list[EvaluationSubmission]] = {}
        for activity in configured_activities:
            code = activity.key
            expected_assignments = assignments_by_recruit_activity.get((recruit.id, code), [])
            expected = len(expected_assignments)
            submitted_records = [
                submissions[item.id]
                for item in expected_assignments
                if item.id in submissions and submissions[item.id].status in {"submitted", "locked"}
            ]
            submitted_by_activity[code] = submitted_records
            submitted_values = [Decimal(item.score) for item in submitted_records]
            submitted = len(submitted_values)
            # Missing expected submissions stay visible/incomplete, while the
            # available evaluator scores are averaged without adding a zero.
            admin_evaluation = admin_evaluations.get((recruit.id, code))
            if admin_evaluation:
                value = Decimal(admin_evaluation.score)
            elif definition.scoring.assessorAggregation == "missing_as_zero" and expected:
                value = sum(submitted_values, Decimal("0")) / Decimal(expected)
            else:
                value = sum(submitted_values, Decimal("0")) / Decimal(submitted) if submitted else Decimal("0")
            activity_values[code] = value
            activity_rank_inputs[code].append((recruit.id, value))
            activities_payload[code] = {
                "score": float(value),
                "expected": expected,
                "submitted": submitted,
                "complete": bool(admin_evaluation) or (expected > 0 and submitted == expected),
                "adminOverride": bool(admin_evaluation),
                "adminEvaluationId": admin_evaluation.id if admin_evaluation else None,
            }

        dimensions_payload: dict[str, dict] = {}
        dimension_values: dict[str, Decimal] = {}
        for dimension_config in definition.dimensions:
            dimension = dimension_config.key
            if dimension_config.source == "activity":
                activity_code = dimension_config.activityKey
                activity_payload = activities_payload.get(activity_code, {})
                value = activity_values.get(activity_code, Decimal("0")) / Decimal("5")
                dimension_values[dimension] = value
                dimension_rank_inputs[dimension].append((recruit.id, value))
                dimensions_payload[dimension] = {
                    "name": dimension_config.name,
                    "score": float(value),
                    "complete": bool(activity_payload.get("complete")),
                    "availableWeight": 1.0 if activity_payload.get("submitted") or activity_payload.get("adminOverride") else 0.0,
                    "criterionCount": len(RUBRICS[activity_code].criteria) if activity_code in RUBRICS else 0,
                }
                continue
            weighted_value = Decimal("0")
            total_weight = Decimal("0")
            available_weight = Decimal("0")
            complete = True
            criterion_count = 0
            for activity in configured_activities:
                code = activity.key
                matching_criteria = [item for item in activity.criteria if item.dimensionKey == dimension]
                if not matching_criteria:
                    continue
                expected_assignments = assignments_by_recruit_activity.get((recruit.id, code), [])
                admin_evaluation = admin_evaluations.get((recruit.id, code))
                submitted_records = submitted_by_activity[code]
                if not admin_evaluation and (not expected_assignments or len(submitted_records) != len(expected_assignments)):
                    complete = False
                response_payloads = (
                    [loads(admin_evaluation.responses_json, {})]
                    if admin_evaluation
                    else [loads(item.responses_json, {}) for item in submitted_records]
                )
                for configured_criterion in matching_criteria:
                    criterion = next(item for item in RUBRICS[code].criteria if item.key == configured_criterion.key)
                    criterion_count += 1
                    total_weight += criterion.weight
                    grades: list[Decimal] = []
                    for response in response_payloads:
                        try:
                            grade = Decimal(str(response[criterion.key]))
                            if (grade < Decimal(configured_criterion.minimum)
                                    or grade > Decimal(configured_criterion.maximum)):
                                raise ValueError
                            grades.append(grade)
                        except (KeyError, TypeError, ValueError, ArithmeticError):
                            complete = False
                    if grades:
                        denominator = (len(expected_assignments)
                                       if definition.scoring.assessorAggregation == "missing_as_zero" and not admin_evaluation
                                       else len(grades))
                        criterion_average = sum(grades, Decimal("0")) / Decimal(max(denominator, 1))
                        available_weight += criterion.weight
                    else:
                        criterion_average = Decimal("0")
                        complete = False
                    minimum = Decimal(configured_criterion.minimum)
                    scale = Decimal(configured_criterion.maximum) - minimum
                    weighted_value += ((criterion_average - minimum) / scale) * criterion.weight
            value = weighted_value / total_weight if total_weight else Decimal("0")
            dimension_values[dimension] = value
            dimension_rank_inputs[dimension].append((recruit.id, value))
            dimensions_payload[dimension] = {
                "name": dimension_config.name,
                "score": float(value),
                "complete": complete,
                "availableWeight": float(available_weight / total_weight) if total_weight else 0.0,
                "criterionCount": criterion_count,
            }

        assessment = assessments.get(recruit.id)
        general_values = {factor.storageKey: getattr(assessment, factor.storageKey) if assessment else None
                          for factor in definition.generalFactors}
        general = general_average(general_values)
        general_missing = sum(1 for value in general_values.values() if value is None)
        complete_components = {("dimension", key) for key, item in dimensions_payload.items() if item["complete"]}
        if general_missing == 0 and definition.generalFactors:
            complete_components.add(("general", "general"))
        overall = overall_score(dimension_values, general, complete_components)
        overall_rank_inputs.append((recruit.id, overall))
        missing_activities = [
            code for code, item in activities_payload.items() if not item["complete"]
        ]
        missing_dimensions = sum(1 for item in dimensions_payload.values() if not item["complete"])
        missing = len(missing_activities) + general_missing
        missing_components = [
            RUBRICS[code].name for code in missing_activities
        ] + [
            factor.name for factor in definition.generalFactors if general_values[factor.storageKey] is None
        ]
        rows.append(
            {
                "recruitId": recruit.id,
                "name": recruit.name,
                "hasPhoto": has_photo(recruit),
                "activities": activities_payload,
                "dimensions": dimensions_payload,
                "generalAverage": float(general),
                "generalComplete": general_missing == 0,
                "generalComment": assessment.comment if assessment else "",
                "notes": assessment.notes if assessment else "",
                "overallScore": float(overall),
                "color": color_grade(overall),
                "missingCount": missing,
                "missingComponents": missing_components,
                "missingActivityCount": len(missing_activities),
                "missingDimensionCount": missing_dimensions,
                "missingGeneralCount": general_missing,
                "complete": missing == 0,
            }
        )

    activity_ranks = {code: configured_ranks(values) for code, values in activity_rank_inputs.items()}
    dimension_ranks = {code: configured_ranks(values) for code, values in dimension_rank_inputs.items()}
    overall_ranks = configured_ranks(overall_rank_inputs)
    for row in rows:
        for code in activity_rank_inputs:
            row["activities"][code]["rank"] = activity_ranks[code].get(row["recruitId"])
        for code in dimension_rank_inputs:
            row["dimensions"][code]["rank"] = dimension_ranks[code].get(row["recruitId"])
        row["overallRank"] = overall_ranks.get(row["recruitId"])
    rows.sort(key=lambda row: (row["overallRank"] or 10**9, row["name"]))
    activity_averages = {
        code: float(sum((score for _identifier, score in values), Decimal("0")) / Decimal(len(values))) if values else 0.0
        for code, values in activity_rank_inputs.items()
    }
    dimension_averages = {
        code: float(sum((score for _identifier, score in values), Decimal("0")) / Decimal(len(values))) if values else 0.0
        for code, values in dimension_rank_inputs.items()
    }
    return {
        "journeyId": journey.id,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "formula": "Configured weighted overall score",
        "scoreMaximum": float(definition.scoring.officialMaximum),
        "dimensionNames": {item.key: item.name for item in definition.dimensions},
        "performanceBands": [item.model_dump(mode="json") for item in definition.scoring.bands],
        "dimensionAverages": dimension_averages,
        "activityAverages": activity_averages,
        "rows": rows,
    }


def monitoring_snapshot(db: Session, journey: Journey, activity_code: str) -> dict:
    state = db.scalar(
        select(ActivityState).where(ActivityState.journey_id == journey.id, ActivityState.code == activity_code)
    )
    if not state:
        raise HTTPException(status_code=404, detail="Activity not found.")
    round_record = db.get(AssignmentRound, state.assignment_round_id) if state.assignment_round_id else None
    assignments = list(db.scalars(select(Assignment).where(Assignment.round_id == round_record.id))) if round_record else []
    assignment_ids = [item.id for item in assignments]
    submissions = {
        item.assignment_id: item
        for item in db.scalars(select(EvaluationSubmission).where(EvaluationSubmission.assignment_id.in_(assignment_ids)))
    } if assignment_ids else {}
    recruits = {item.id: item for item in db.scalars(select(Recruit).where(Recruit.journey_id == journey.id))}
    evaluators = {item.id: item for item in db.scalars(select(Evaluator).where(Evaluator.journey_id == journey.id))}
    recruit_groups: dict[str, list[Assignment]] = defaultdict(list)
    evaluator_groups: dict[str, list[Assignment]] = defaultdict(list)
    for item in assignments:
        recruit_groups[item.recruit_id].append(item)
        evaluator_groups[item.evaluator_id].append(item)
    admin_evaluations = {
        item.recruit_id: item
        for item in db.scalars(
            select(AdminEvaluation).where(
                AdminEvaluation.journey_id == journey.id,
                AdminEvaluation.activity_code == activity_code,
            )
        )
    }
    visible_recruits = [
        item for item in recruits.values() if item.active and item.present
    ]
    return {
        "activityCode": activity_code,
        "activityName": RUBRICS[activity_code].name,
        "status": state.status,
        "roundVersion": round_record.version if round_record else None,
        "recruits": [
            {
                "id": recruit.id,
                "name": recruit.name,
                "expected": len(items),
                "submitted": sum(1 for item in items if item.id in submissions and submissions[item.id].status in {"submitted", "locked"}),
                "adminEvaluation": ({
                    "id": admin_evaluations[recruit.id].id,
                    "score": float(admin_evaluations[recruit.id].score),
                    "version": admin_evaluations[recruit.id].version,
                    "updatedBy": admin_evaluations[recruit.id].updated_by,
                } if recruit.id in admin_evaluations else None),
                "tasks": [
                    {
                        "evaluatorName": evaluators[item.evaluator_id].name if item.evaluator_id in evaluators else "Unknown",
                        "submissionId": submissions[item.id].id if item.id in submissions else None,
                        "submissionStatus": submissions[item.id].status if item.id in submissions else None,
                        "score": float(submissions[item.id].score) if item.id in submissions else None,
                    }
                    for item in items
                ],
            }
            for recruit in sorted(visible_recruits, key=lambda value: value.name.casefold())
            for items in [recruit_groups.get(recruit.id, [])]
        ],
        "evaluators": [
            {
                "id": evaluator_id,
                "name": evaluators[evaluator_id].name if evaluator_id in evaluators else "Unknown",
                "role": evaluators[evaluator_id].role if evaluator_id in evaluators else "",
                "tasks": [
                    {
                        "assignmentId": item.id,
                        "taskKey": item.task_key or item.id,
                        "recruitId": item.recruit_id,
                        "recruitName": recruits[item.recruit_id].name if item.recruit_id in recruits else "Unknown",
                        "submitted": item.id in submissions and submissions[item.id].status in {"submitted", "locked"},
                        "submissionId": submissions[item.id].id if item.id in submissions else None,
                        "submissionStatus": submissions[item.id].status if item.id in submissions else None,
                        "score": float(submissions[item.id].score) if item.id in submissions else None,
                        "comments": submissions[item.id].comments if item.id in submissions else "",
                        "submittedAt": submissions[item.id].submitted_at.isoformat() if item.id in submissions and submissions[item.id].submitted_at else None,
                    }
                    for item in items
                ],
            }
            for evaluator_id, items in evaluator_groups.items()
        ],
    }


def score_evaluation(activity_code: str, responses: dict, raw: dict) -> tuple[Decimal, dict, dict]:
    try:
        if RUBRICS[activity_code].kind == "sport":
            source = raw or responses
            score, normalized, exercise_scores = target_activity_score(activity_code, source)
            return score, {key: float(value) for key, value in exercise_scores.items()}, {
                key: float(value) for key, value in normalized.items()
            }
        score, normalized = weighted_activity_score(activity_code, responses)
        return score, {key: float(value) for key, value in normalized.items()}, raw
    except ScoringError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def save_submission(
    db: Session,
    *,
    assignment: Assignment,
    round_record: AssignmentRound,
    state: ActivityState,
    responses: dict,
    raw: dict,
    comments: str,
    actor_type: str,
    actor_name: str,
    submit: bool,
    reason: str = "",
) -> EvaluationSubmission:
    if actor_type == "evaluator" and state.status != "open":
        raise HTTPException(status_code=409, detail="This activity is not open for editing.")
    if submit:
        score, normalized_responses, normalized_raw = score_evaluation(round_record.activity_code, responses, raw)
    else:
        # Drafts are deliberately allowed to be incomplete.  Keep the original
        # fields so a form can be restored after a reload or a brief outage.  A
        # complete draft gets a provisional score; an incomplete one remains 0.
        normalized_responses = dict(responses)
        normalized_raw = dict(raw)
        try:
            score, normalized_responses, normalized_raw = score_evaluation(
                round_record.activity_code, responses, raw
            )
        except HTTPException as exc:
            if exc.status_code != 422:
                raise
            score = Decimal("0")
    submission = db.scalar(select(EvaluationSubmission).where(EvaluationSubmission.assignment_id == assignment.id))
    if submission is None:
        submission = EvaluationSubmission(
            assignment_id=assignment.id,
            journey_id=round_record.journey_id,
            activity_code=round_record.activity_code,
            evaluator_id=assignment.evaluator_id,
            recruit_id=assignment.recruit_id,
        )
        db.add(submission)
        db.flush()
    else:
        submission.version += 1
    submission.responses_json = dumps(normalized_responses)
    submission.raw_payload_json = dumps(normalized_raw)
    submission.comments = comments.strip()
    submission.score = score
    submission.status = "submitted" if submit else "draft"
    if submit:
        submission.submitted_at = utcnow()
    db.add(
        SubmissionVersion(
            submission_id=submission.id,
            version=submission.version,
            payload_json=dumps({"responses": normalized_responses, "raw": normalized_raw, "comments": comments, "status": submission.status}),
            score=score,
            actor_type=actor_type,
            actor_name=actor_name,
            reason=reason,
        )
    )
    if submit or actor_type == "admin":
        audit(
            db,
            journey_id=round_record.journey_id,
            actor_type=actor_type,
            actor_name=actor_name,
            action="evaluation.submitted" if submit else "evaluation.draft_saved",
            entity_type="evaluation_submission",
            entity_id=submission.id,
            after={"activity": round_record.activity_code, "score": score, "version": submission.version},
            reason=reason,
        )
    return submission
