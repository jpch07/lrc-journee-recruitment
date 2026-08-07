from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import UserContext, require_csrf, require_results
from .db import get_db
from .models import Evaluator, Journey, MandatoryRoomEvaluator, Recruit
from .routes_admin import (
    _activity_states,
    export_results_xlsx,
    export_xlsx,
    recruit_profile,
    save_general_assessment,
    submission_detail,
)
from .schemas import GeneralAssessmentRequest
from .services import (
    get_journey_or_404,
    result_snapshot,
    serialize_evaluator,
    serialize_journey,
    serialize_recruit,
)

router = APIRouter(prefix="/api/view", tags=["read-only-management"])


@router.get("/journeys")
def journeys(context: UserContext = Depends(require_results), db: Session = Depends(get_db)):
    del context
    return [serialize_journey(db, item) for item in db.scalars(
        select(Journey).order_by(Journey.event_date.desc(), func.lower(Journey.name))
    )]


@router.get("/journeys/{journey_id}")
def journey_view(journey_id: str, context: UserContext = Depends(require_results), db: Session = Depends(get_db)):
    del context
    journey = get_journey_or_404(db, journey_id)
    recruits = list(db.scalars(select(Recruit).where(
        Recruit.journey_id == journey.id, Recruit.active.is_(True)
    ).order_by(func.lower(Recruit.name))))
    mandatory_rooms = {
        item.evaluator_id: item.room_number
        for item in db.scalars(select(MandatoryRoomEvaluator).where(
            MandatoryRoomEvaluator.journey_id == journey.id
        ))
    }
    evaluators = []
    for item in db.scalars(select(Evaluator).where(
        Evaluator.journey_id == journey.id, Evaluator.active.is_(True)
    ).order_by((Evaluator.role == "dossard"), func.lower(Evaluator.name))):
        payload = serialize_evaluator(item)
        payload["mandatoryRoom"] = mandatory_rooms.get(item.id)
        evaluators.append(payload)
    return {
        "journey": serialize_journey(db, journey),
        "activities": _activity_states(db, journey.id),
        "recruits": [serialize_recruit(item) for item in recruits],
        "evaluators": evaluators,
        "results": result_snapshot(db, journey),
    }


@router.get("/journeys/{journey_id}/recruits/{recruit_id}/profile")
def profile_view(journey_id: str, recruit_id: str, context: UserContext = Depends(require_results), db: Session = Depends(get_db)):
    del context
    payload = recruit_profile(journey_id, recruit_id, context=None, db=db)
    if payload.get("photoUrl"):
        payload["photoUrl"] = f"/api/view/journeys/{journey_id}/recruits/{recruit_id}/photo"
    return payload


@router.put("/journeys/{journey_id}/recruits/{recruit_id}/profile")
def update_profile_general_assessment(
    journey_id: str,
    recruit_id: str,
    payload: GeneralAssessmentRequest,
    request: Request,
    context: UserContext = Depends(require_results),
    db: Session = Depends(get_db),
):
    require_csrf(request, context.csrf_token)
    return save_general_assessment(
        db,
        journey_id=journey_id,
        recruit_id=recruit_id,
        payload=payload,
        actor_name=context.username,
        actor_type="results",
    )


@router.get("/journeys/{journey_id}/recruits/{recruit_id}/photo")
def profile_photo(journey_id: str, recruit_id: str, context: UserContext = Depends(require_results), db: Session = Depends(get_db)):
    del context
    recruit = db.get(Recruit, recruit_id)
    if not recruit or recruit.journey_id != journey_id or not recruit.photo_data:
        raise HTTPException(status_code=404, detail="Photo not found.")
    return Response(recruit.photo_data, media_type=recruit.photo_type or "image/webp", headers={"Cache-Control": "private, no-store"})


@router.get("/journeys/{journey_id}/submissions/{submission_id}")
def read_submission(
    journey_id: str,
    submission_id: str,
    context: UserContext = Depends(require_results),
    db: Session = Depends(get_db),
):
    del context
    return submission_detail(journey_id, submission_id, context=None, db=db)


@router.get("/journeys/{journey_id}/export.xlsx")
def read_full_export(
    journey_id: str,
    context: UserContext = Depends(require_results),
    db: Session = Depends(get_db),
):
    del context
    return export_xlsx(journey_id, context=None, db=db)


@router.get("/journeys/{journey_id}/results.xlsx")
def read_results_export(
    journey_id: str,
    context: UserContext = Depends(require_results),
    db: Session = Depends(get_db),
):
    del context
    return export_results_xlsx(journey_id, context=None, db=db)
