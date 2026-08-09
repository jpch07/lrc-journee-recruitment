from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import (
    RecruitAttendanceContext,
    UserContext,
    clear_expired_sessions,
    create_recruit_attendance_session,
    logout_recruit_attendance,
    require_csrf,
    require_recruit_attendance,
    require_user,
)
from .config import settings
from .db import get_db
from .models import (
    Assignment,
    GeneralAssessment,
    Journey,
    JourneyPermission,
    Recruit,
    RecruitAttendanceAccess,
    RoomPlanRecruit,
    utcnow,
)
from .schemas import (
    RecruitAttendanceRequest,
    RecruitAttendancePatchRequest,
    RecruitAttendanceSessionRequest,
    RecruitFromDirectoryRequest,
)
from .recruit_directory import create_recruit_from_directory, ensure_recruit_directory
from .services import (
    get_recruit_or_404,
    process_photo,
    serialize_recruit,
    update_recruit_attendance_fields,
)
from .utils import audit


router = APIRouter(tags=["recruit-attendance"])
OPEN_ATTENDANCE_STATUSES = {"draft", "ready", "active"}


def _commit(db: Session) -> None:
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


def _access_journey(db: Session, token: str) -> Journey:
    access = db.scalar(select(RecruitAttendanceAccess).where(RecruitAttendanceAccess.token == token))
    journey = db.get(Journey, access.journey_id) if access else None
    if not journey or journey.status not in OPEN_ATTENDANCE_STATUSES:
        raise HTTPException(status_code=404, detail="This recruit attendance link is invalid or no longer active.")
    return journey


def _session_journey(db: Session, context: RecruitAttendanceContext) -> Journey:
    journey = db.get(Journey, context.journey_id)
    if not journey or journey.status not in OPEN_ATTENDANCE_STATUSES:
        raise HTTPException(status_code=401, detail="Recruit attendance is closed for this Journee.")
    return journey


def _landing_payload(journey: Journey) -> dict:
    return {
        "journeyId": journey.id,
        "name": journey.name,
        "eventDate": journey.event_date.isoformat(),
        "status": journey.status,
    }


@router.get("/api/public/recruit-attendance/{token}")
def resolve_recruit_attendance_link(token: str, db: Session = Depends(get_db)):
    return _landing_payload(_access_journey(db, token))


@router.post("/api/public/recruit-attendance/{token}/session")
def start_recruit_attendance_session(
    token: str,
    payload: RecruitAttendanceSessionRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    if settings.is_production:
        raise HTTPException(status_code=404, detail="Named account login is required.")
    journey = _access_journey(db, token)
    clear_expired_sessions(db)
    session = create_recruit_attendance_session(db, response, journey.id, payload.display_name)
    audit(
        db,
        journey_id=journey.id,
        actor_type="attendance",
        actor_name=session.actor_name,
        action="attendance.session_started",
        entity_type="attendance_session",
    )
    _commit(db)
    return {
        "ok": True,
        "journeyId": journey.id,
        "journeyName": journey.name,
        "displayName": session.actor_name,
        "csrfToken": session.csrf_token,
    }


@router.post("/api/public/recruit-attendance/{token}/select")
def select_recruit_attendance_journey(
    token: str,
    response: Response,
    context: UserContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    journey = _access_journey(db, token)
    permission = db.scalar(
        select(JourneyPermission).where(
            JourneyPermission.account_id == context.account_id,
            JourneyPermission.journey_id == journey.id,
            JourneyPermission.can_attendance.is_(True),
        )
    )
    if not (context.is_owner or context.can_admin or permission):
        raise HTTPException(status_code=403, detail="You do not have attendance access for this Journee.")
    response.set_cookie(
        "lrc_attendance_journey",
        journey.id,
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return {
        "authenticated": True,
        "journeyId": journey.id,
        "journeyName": journey.name,
        "displayName": context.username,
        "csrfToken": context.csrf_token,
    }


@router.get("/api/recruit-attendance/session")
def recruit_attendance_session(
    context: RecruitAttendanceContext = Depends(require_recruit_attendance),
    db: Session = Depends(get_db),
):
    journey = _session_journey(db, context)
    return {
        "authenticated": True,
        "journeyId": journey.id,
        "journeyName": journey.name,
        "displayName": context.actor_name,
        "csrfToken": context.csrf_token,
    }


@router.post("/api/recruit-attendance/logout")
def recruit_attendance_logout(
    request: Request,
    response: Response,
    context: RecruitAttendanceContext = Depends(require_recruit_attendance),
    db: Session = Depends(get_db),
):
    require_csrf(request, context.csrf_token)
    logout_recruit_attendance(db, response, context)
    _commit(db)
    return {"ok": True}


@router.get("/api/recruit-attendance/recruits")
def recruit_attendance_recruits(
    context: RecruitAttendanceContext = Depends(require_recruit_attendance),
    db: Session = Depends(get_db),
):
    journey = _session_journey(db, context)
    recruits = list(
        db.scalars(
            select(Recruit)
            .where(Recruit.journey_id == journey.id, Recruit.active.is_(True))
            .order_by(func.lower(Recruit.name))
        )
    )
    return {
        "journey": _landing_payload(journey),
        "recruits": [serialize_recruit(item) for item in recruits],
        "serverTime": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/recruit-attendance/directory")
def recruit_attendance_directory(
    context: RecruitAttendanceContext = Depends(require_recruit_attendance),
    db: Session = Depends(get_db),
):
    _session_journey(db, context)
    return ensure_recruit_directory(db)


@router.post("/api/recruit-attendance/recruits/from-directory")
def add_recruit_from_attendance(
    payload: RecruitFromDirectoryRequest,
    request: Request,
    context: RecruitAttendanceContext = Depends(require_recruit_attendance),
    db: Session = Depends(get_db),
):
    require_csrf(request, context.csrf_token)
    journey = _session_journey(db, context)
    recruit = create_recruit_from_directory(db, journey.id, payload.directory_id)
    audit(
        db,
        journey_id=journey.id,
        actor_type="attendance",
        actor_name=context.actor_name,
        action="recruit.added_from_directory",
        entity_type="recruit",
        entity_id=recruit.id,
        after=serialize_recruit(recruit),
    )
    _commit(db)
    return serialize_recruit(recruit)


@router.put("/api/recruit-attendance/recruits")
def save_recruit_attendance(
    payload: RecruitAttendanceRequest,
    request: Request,
    context: RecruitAttendanceContext = Depends(require_recruit_attendance),
    db: Session = Depends(get_db),
):
    require_csrf(request, context.csrf_token)
    journey = _session_journey(db, context)
    changed = []
    for item in payload.items:
        recruit = get_recruit_or_404(db, journey.id, item.id)
        if item.base_version is not None and item.base_version != recruit.version:
            raise HTTPException(
                status_code=409,
                detail=f"{recruit.name} was changed by another user. Reload attendance before saving.",
            )
        before = serialize_recruit(recruit)
        recruit.present = item.present
        recruit.arrival_time = item.arrival_time if item.present else None
        recruit.phone_number = (item.phone_number or "").strip() or None
        if "date_of_birth" in item.model_fields_set:
            recruit.date_of_birth = item.date_of_birth
        recruit.attendance_comment = item.attendance_comment.strip()
        recruit.active = item.active
        recruit.version += 1
        changed.append({"before": before, "after": serialize_recruit(recruit)})
    audit(
        db,
        journey_id=journey.id,
        actor_type="attendance",
        actor_name=context.actor_name,
        action="attendance.recruits_saved",
        entity_type="attendance",
        after=changed,
    )
    _commit(db)
    return {
        "ok": True,
        "items": [
            serialize_recruit(get_recruit_or_404(db, journey.id, item.id))
            for item in payload.items
        ],
    }


@router.patch("/api/recruit-attendance/recruits/{recruit_id}")
def auto_save_recruit_attendance(
    recruit_id: str,
    payload: RecruitAttendancePatchRequest,
    request: Request,
    context: RecruitAttendanceContext = Depends(require_recruit_attendance),
    db: Session = Depends(get_db),
):
    require_csrf(request, context.csrf_token)
    journey = _session_journey(db, context)
    changes = payload.model_dump(exclude={"base_version"}, exclude_unset=True)
    before, recruit = update_recruit_attendance_fields(
        db, journey.id, recruit_id, payload.base_version, changes
    )
    audit(
        db,
        journey_id=journey.id,
        actor_type="attendance",
        actor_name=context.actor_name,
        action="attendance.recruit_auto_saved",
        entity_type="recruit",
        entity_id=recruit.id,
        before=before,
        after=serialize_recruit(recruit),
    )
    _commit(db)
    return serialize_recruit(recruit)


@router.delete("/api/recruit-attendance/recruits/{recruit_id}")
def remove_recruit_from_attendance(
    recruit_id: str,
    request: Request,
    context: RecruitAttendanceContext = Depends(require_recruit_attendance),
    db: Session = Depends(get_db),
):
    require_csrf(request, context.csrf_token)
    journey = _session_journey(db, context)
    recruit = get_recruit_or_404(db, journey.id, recruit_id)
    referenced = bool(
        db.scalar(select(Assignment.id).where(Assignment.recruit_id == recruit.id).limit(1))
        or db.scalar(select(RoomPlanRecruit.id).where(RoomPlanRecruit.recruit_id == recruit.id).limit(1))
        or db.get(GeneralAssessment, recruit.id)
    )
    disposition = "deactivated" if referenced else "deleted"
    audit(
        db,
        journey_id=journey.id,
        actor_type="attendance",
        actor_name=context.actor_name,
        action=f"recruit.{disposition}",
        entity_type="recruit",
        entity_id=recruit.id,
        before=serialize_recruit(recruit),
        reason="Removed from recruit attendance workspace.",
    )
    if referenced:
        recruit.active = False
        recruit.present = False
        recruit.arrival_time = None
        recruit.version += 1
    else:
        db.delete(recruit)
    _commit(db)
    return {"ok": True, "disposition": disposition}


@router.get("/api/recruit-attendance/recruits/{recruit_id}/photo")
def recruit_attendance_photo(
    recruit_id: str,
    context: RecruitAttendanceContext = Depends(require_recruit_attendance),
    db: Session = Depends(get_db),
):
    journey = _session_journey(db, context)
    recruit = get_recruit_or_404(db, journey.id, recruit_id)
    if not recruit.active or not recruit.photo_data:
        raise HTTPException(status_code=404, detail="No photo available.")
    return Response(
        content=recruit.photo_data,
        media_type=recruit.photo_type or "image/webp",
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.post("/api/recruit-attendance/recruits/{recruit_id}/photo")
def upload_recruit_attendance_photo(
    recruit_id: str,
    request: Request,
    photo: UploadFile = File(...),
    context: RecruitAttendanceContext = Depends(require_recruit_attendance),
    db: Session = Depends(get_db),
):
    require_csrf(request, context.csrf_token)
    journey = _session_journey(db, context)
    recruit = get_recruit_or_404(db, journey.id, recruit_id)
    if not recruit.active:
        raise HTTPException(status_code=404, detail="Recruit not found.")
    before = {"hasPhoto": bool(recruit.photo_data), "version": recruit.version}
    data, media_type = process_photo(photo.file.read())
    recruit.photo_data = data
    recruit.photo_type = media_type
    recruit.photo_updated_at = utcnow()
    recruit.version += 1
    audit(
        db,
        journey_id=journey.id,
        actor_type="attendance",
        actor_name=context.actor_name,
        action="recruit.photo_updated",
        entity_type="recruit",
        entity_id=recruit.id,
        before=before,
        after={"hasPhoto": True, "version": recruit.version},
    )
    _commit(db)
    return serialize_recruit(recruit)


@router.delete("/api/recruit-attendance/recruits/{recruit_id}/photo")
def remove_recruit_attendance_photo(
    recruit_id: str,
    request: Request,
    context: RecruitAttendanceContext = Depends(require_recruit_attendance),
    db: Session = Depends(get_db),
):
    require_csrf(request, context.csrf_token)
    journey = _session_journey(db, context)
    recruit = get_recruit_or_404(db, journey.id, recruit_id)
    if not recruit.active or not recruit.photo_data:
        raise HTTPException(status_code=404, detail="No photo available.")
    before = {"hasPhoto": True, "version": recruit.version}
    recruit.photo_data = None
    recruit.photo_type = None
    recruit.photo_updated_at = utcnow()
    recruit.version += 1
    audit(
        db,
        journey_id=journey.id,
        actor_type="attendance",
        actor_name=context.actor_name,
        action="recruit.photo_removed",
        entity_type="recruit",
        entity_id=recruit.id,
        before=before,
        after={"hasPhoto": False, "version": recruit.version},
    )
    _commit(db)
    return serialize_recruit(recruit)
