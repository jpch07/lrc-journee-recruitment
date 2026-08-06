from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import (
    EvaluatorContext,
    clear_expired_sessions,
    create_evaluator_session,
    logout_evaluator,
    require_csrf,
    require_evaluator,
)
from .db import get_db
from .models import (
    ActivityState,
    Assignment,
    AssignmentRound,
    EvaluationSubmission,
    Evaluator,
    EvaluatorSession,
    IdempotencyRecord,
    Journey,
    Recruit,
    RoomPlanEvaluator,
)
from .rubric import ACTIVITY_ORDER, RUBRICS, evaluator_rubric
from .schemas import EvaluationPayload, EvaluatorSessionRequest
from .services import latest_room_plan, save_submission
from .utils import dumps, loads


router = APIRouter(tags=["evaluator"])


def _commit(db: Session) -> None:
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


def _public_journey(db: Session, token: str) -> Journey:
    journey = db.scalar(select(Journey).where(Journey.public_token == token))
    if not journey or journey.status != "active":
        raise HTTPException(status_code=404, detail="This evaluator link is invalid or no longer active.")
    return journey


def _current_journey(db: Session) -> Journey:
    journeys = list(db.scalars(select(Journey).where(Journey.status == "active").limit(2)))
    if not journeys:
        raise HTTPException(status_code=404, detail="No Journee is currently active.")
    if len(journeys) > 1:
        raise HTTPException(status_code=409, detail="More than one Journee is active; an admin must correct the status.")
    return journeys[0]


def _landing_payload(db: Session, journey: Journey) -> dict:
    evaluators = list(
        db.scalars(
            select(Evaluator)
            .where(Evaluator.journey_id == journey.id, Evaluator.active.is_(True), Evaluator.present.is_(True))
            .order_by(Evaluator.name)
        )
    )
    return {"name": journey.name, "eventDate": journey.event_date.isoformat(), "status": journey.status,
            "evaluators": [{"id": item.id, "name": item.name} for item in evaluators]}


def _session_entities(db: Session, context: EvaluatorContext) -> tuple[Journey, Evaluator]:
    journey = db.get(Journey, context.journey_id)
    evaluator = db.get(Evaluator, context.evaluator_id)
    if not journey or journey.status != "active" or not evaluator or not evaluator.active or not evaluator.present:
        raise HTTPException(status_code=401, detail="This evaluator session is no longer active.")
    return journey, evaluator


def _submission_summary(submission: EvaluationSubmission | None) -> dict | None:
    if not submission:
        return None
    return {
        "id": submission.id,
        "status": submission.status,
        "responses": loads(submission.responses_json, {}),
        "raw": loads(submission.raw_payload_json, {}),
        "comments": submission.comments,
        "score": float(submission.score),
        "version": submission.version,
        "submittedAt": submission.submitted_at.isoformat() if submission.submitted_at else None,
        "updatedAt": submission.updated_at.isoformat(),
    }


@router.get("/api/public/journeys/{token}")
def resolve_journey(token: str, db: Session = Depends(get_db)):
    journey = _public_journey(db, token)
    return _landing_payload(db, journey)


@router.get("/api/public/current")
def resolve_current_journey(db: Session = Depends(get_db)):
    return _landing_payload(db, _current_journey(db))


@router.post("/api/public/journeys/{token}/session")
def start_evaluator_session(
    token: str,
    payload: EvaluatorSessionRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    journey = _public_journey(db, token)
    evaluator = db.get(Evaluator, payload.evaluator_id)
    if (
        not evaluator
        or evaluator.journey_id != journey.id
        or not evaluator.active
        or not evaluator.present
    ):
        raise HTTPException(status_code=404, detail="This evaluator is not present in the selected Journee.")
    clear_expired_sessions(db)
    session = create_evaluator_session(db, response, journey.id, evaluator.id)
    _commit(db)
    return {
        "ok": True,
        "evaluatorName": evaluator.name,
        "csrfToken": session.csrf_token,
    }


@router.post("/api/public/current/session")
def start_current_evaluator_session(
    payload: EvaluatorSessionRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    journey = _current_journey(db)
    evaluator = db.get(Evaluator, payload.evaluator_id)
    if not evaluator or evaluator.journey_id != journey.id or not evaluator.active or not evaluator.present:
        raise HTTPException(status_code=404, detail="This evaluator is not present in the active Journee.")
    clear_expired_sessions(db)
    session = create_evaluator_session(db, response, journey.id, evaluator.id)
    _commit(db)
    return {"ok": True, "evaluatorName": evaluator.name, "csrfToken": session.csrf_token}


@router.get("/api/evaluator/session")
def evaluator_session(
    context: EvaluatorContext = Depends(require_evaluator),
    db: Session = Depends(get_db),
):
    journey, evaluator = _session_entities(db, context)
    return {
        "authenticated": True,
        "journeyName": journey.name,
        "evaluatorName": evaluator.name,
        "csrfToken": context.csrf_token,
    }


@router.post("/api/evaluator/logout")
def evaluator_logout(
    request: Request,
    response: Response,
    context: EvaluatorContext = Depends(require_evaluator),
    db: Session = Depends(get_db),
):
    require_csrf(request, context.csrf_token)
    logout_evaluator(db, response, context)
    _commit(db)
    return {"ok": True}


def _home_payload(db: Session, journey: Journey, evaluator: Evaluator) -> dict:
    states = {
        item.code: item
        for item in db.scalars(select(ActivityState).where(ActivityState.journey_id == journey.id))
    }
    room_number = None
    room_plan = latest_room_plan(db, journey.id, "published")
    if room_plan:
        room_member = db.scalar(
            select(RoomPlanEvaluator).where(
                RoomPlanEvaluator.plan_id == room_plan.id,
                RoomPlanEvaluator.evaluator_id == evaluator.id,
            )
        )
        room_number = room_member.room_number if room_member else None

    activities: list[dict] = []
    for code in ACTIVITY_ORDER:
        state = states.get(code)
        assignments: list[Assignment] = []
        if state and state.assignment_round_id:
            assignments = list(
                db.scalars(
                    select(Assignment).where(
                        Assignment.round_id == state.assignment_round_id,
                        Assignment.evaluator_id == evaluator.id,
                    )
                )
            )
        tasks = []
        for assignment in assignments:
            recruit = db.get(Recruit, assignment.recruit_id)
            submission = db.scalar(
                select(EvaluationSubmission).where(EvaluationSubmission.assignment_id == assignment.id)
            )
            if not recruit:
                continue
            task_status = "closed" if state.status == "closed" else (
                "submitted" if submission and submission.status in {"submitted", "locked"} else (
                    "draft" if submission else "start"
                )
            )
            tasks.append(
                {
                    "assignmentId": assignment.id,
                    "recruitId": recruit.id,
                    "recruitName": recruit.name,
                    "hasPhoto": bool(recruit.photo_data),
                    "photoUrl": f"/api/evaluator/tasks/{assignment.id}/photo" if recruit.photo_data else None,
                    "roomNumber": assignment.room_number,
                    "slot": assignment.slot,
                    "status": task_status,
                    "submission": _submission_summary(submission),
                }
            )
        activities.append(
            {
                "code": code,
                "name": RUBRICS[code].name,
                "status": state.status if state else "not_started",
                "version": state.version if state else 0,
                "rubric": evaluator_rubric(code),
                "tasks": tasks,
            }
        )
    return {
        "journey": {
            "name": journey.name,
            "eventDate": journey.event_date.isoformat(),
            "status": journey.status,
            "currentActivity": journey.current_activity,
            "version": journey.version,
        },
        "evaluator": {
            "id": evaluator.id,
            "name": evaluator.name,
            "role": evaluator.role,
            "roomNumber": room_number,
        },
        "activities": activities,
        "serverTime": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/evaluator/home")
def evaluator_home(
    context: EvaluatorContext = Depends(require_evaluator),
    db: Session = Depends(get_db),
):
    journey, evaluator = _session_entities(db, context)
    return _home_payload(db, journey, evaluator)


@router.get("/api/evaluator/updates")
def evaluator_updates(
    context: EvaluatorContext = Depends(require_evaluator),
    db: Session = Depends(get_db),
):
    journey, evaluator = _session_entities(db, context)
    states = list(db.scalars(select(ActivityState).where(ActivityState.journey_id == journey.id)))
    return {
        "journeyVersion": journey.version,
        "currentActivity": journey.current_activity,
        "activityVersions": {item.code: item.version for item in states},
        "evaluatorVersion": evaluator.version,
    }


def _assigned_task(
    db: Session,
    context: EvaluatorContext,
    assignment_id: str,
) -> tuple[Journey, Evaluator, Assignment, AssignmentRound, ActivityState]:
    journey, evaluator = _session_entities(db, context)
    assignment = db.get(Assignment, assignment_id)
    if not assignment or assignment.evaluator_id != evaluator.id:
        raise HTTPException(status_code=404, detail="Assigned task not found.")
    round_record = db.get(AssignmentRound, assignment.round_id)
    if not round_record or round_record.journey_id != journey.id or round_record.status != "published":
        raise HTTPException(status_code=404, detail="This assignment is no longer current.")
    state = db.scalar(
        select(ActivityState).where(
            ActivityState.journey_id == journey.id,
            ActivityState.code == round_record.activity_code,
            ActivityState.assignment_round_id == round_record.id,
        )
    )
    if not state:
        raise HTTPException(status_code=404, detail="This assignment is no longer current.")
    return journey, evaluator, assignment, round_record, state


@router.get("/api/evaluator/tasks/{assignment_id}/photo")
def assigned_recruit_photo(
    assignment_id: str,
    context: EvaluatorContext = Depends(require_evaluator),
    db: Session = Depends(get_db),
):
    _journey, _evaluator, assignment, _round, _state = _assigned_task(db, context, assignment_id)
    recruit = db.get(Recruit, assignment.recruit_id)
    if not recruit or not recruit.photo_data:
        raise HTTPException(status_code=404, detail="No photo available.")
    return Response(
        content=recruit.photo_data,
        media_type=recruit.photo_type or "image/webp",
        headers={"Cache-Control": "private, max-age=120"},
    )


def _save_task(
    assignment_id: str,
    payload: EvaluationPayload,
    request: Request,
    submit: bool,
    context: EvaluatorContext,
    db: Session,
):
    require_csrf(request, context.csrf_token)
    _journey, evaluator, assignment, round_record, state = _assigned_task(db, context, assignment_id)
    assignment = db.scalar(
        select(Assignment).where(Assignment.id == assignment.id).with_for_update()
    )
    request_key = request.headers.get("Idempotency-Key", "").strip()[:120]
    scope = f"evaluation:{assignment.id}:{'submit' if submit else 'draft'}"
    if request_key:
        prior = db.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.scope == scope,
                IdempotencyRecord.request_key == request_key,
            )
        )
        if prior:
            return loads(prior.response_json, {"ok": True})

    existing = db.scalar(
        select(EvaluationSubmission).where(EvaluationSubmission.assignment_id == assignment.id)
    )
    if existing and payload.client_version is not None and payload.client_version != existing.version:
        raise HTTPException(status_code=409, detail="This evaluation changed in another tab. Reload it before saving.")

    submission = save_submission(
        db,
        assignment=assignment,
        round_record=round_record,
        state=state,
        responses=payload.responses,
        raw=payload.raw,
        comments=payload.comments,
        actor_type="evaluator",
        actor_name=evaluator.name,
        submit=submit,
    )
    result = {"ok": True, "submission": _submission_summary(submission)}
    if request_key:
        db.add(IdempotencyRecord(scope=scope, request_key=request_key, response_json=dumps(result)))
    _commit(db)
    return result


@router.put("/api/evaluator/tasks/{assignment_id}/draft")
def save_task_draft(
    assignment_id: str,
    payload: EvaluationPayload,
    request: Request,
    context: EvaluatorContext = Depends(require_evaluator),
    db: Session = Depends(get_db),
):
    return _save_task(assignment_id, payload, request, False, context, db)


@router.post("/api/evaluator/tasks/{assignment_id}/submit")
def submit_task(
    assignment_id: str,
    payload: EvaluationPayload,
    request: Request,
    context: EvaluatorContext = Depends(require_evaluator),
    db: Session = Depends(get_db),
):
    return _save_task(assignment_id, payload, request, True, context, db)
