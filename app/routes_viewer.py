from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import io

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import UserContext, require_csrf, require_results
from .db import get_db
from .models import Evaluator, Journey, MandatoryRoomEvaluator, Recruit
from .rubric import ACTIVITY_ORDER, DIMENSION_NAMES, DIMENSION_ORDER, RUBRICS
from .scoring import configured_ranks
from .assessment_runtime import active_assessment_definition
from .report_exports import build_management_report_workbook, save_management_report
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
from .object_storage import has_photo, read_recruit_photo

router = APIRouter(prefix="/api/view", tags=["read-only-management"])


def _journey_payload(db: Session, journey: Journey) -> dict:
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
    evaluator_items = list(db.scalars(select(Evaluator).where(
        Evaluator.journey_id == journey.id, Evaluator.active.is_(True)
    )))
    category_order = {item.key: item.primaryPriority for item in active_assessment_definition().assessors.categories}
    evaluator_items.sort(key=lambda item: (category_order.get(item.role, 999), item.name.casefold()))
    for item in evaluator_items:
        payload = serialize_evaluator(item)
        payload["mandatoryRoom"] = mandatory_rooms.get(item.id)
        evaluators.append(payload)
    journey_payload = serialize_journey(db, journey)
    for recruit in recruits:
        payload = serialize_recruit(recruit)
        payload.update({
            "journeyId": journey.id,
            "journeyName": journey.name,
            "journeyDate": journey.event_date.isoformat(),
            "profileKey": f"{journey.id}:{recruit.id}",
        })
        yield "recruit", payload
    for evaluator in evaluators:
        evaluator.update({
            "journeyId": journey.id,
            "journeyName": journey.name,
            "journeyDate": journey.event_date.isoformat(),
        })
        yield "evaluator", evaluator
    yield "meta", {
        "journey": journey_payload,
        "activities": _activity_states(db, journey.id),
        "results": result_snapshot(db, journey),
    }


def _single_journey_view(db: Session, journey: Journey) -> dict:
    recruits: list[dict] = []
    evaluators: list[dict] = []
    meta: dict = {}
    for kind, payload in _journey_payload(db, journey):
        if kind == "recruit":
            recruits.append(payload)
        elif kind == "evaluator":
            evaluators.append(payload)
        else:
            meta = payload
    for row in meta["results"]["rows"]:
        row.update({
            "journeyId": journey.id,
            "journeyName": journey.name,
            "journeyDate": journey.event_date.isoformat(),
            "profileKey": f"{journey.id}:{row['recruitId']}",
        })
    return {
        "scope": "journey",
        **meta,
        "recruits": recruits,
        "evaluators": evaluators,
    }


def _aggregate_results(snapshots: list[tuple[Journey, dict]]) -> dict:
    rows: list[dict] = []
    for journey, snapshot in snapshots:
        for source in snapshot["rows"]:
            row = deepcopy(source)
            row.update({
                "journeyId": journey.id,
                "journeyName": journey.name,
                "journeyDate": journey.event_date.isoformat(),
                "profileKey": f"{journey.id}:{row['recruitId']}",
            })
            rows.append(row)

    overall_inputs = [(row["profileKey"], Decimal(str(row["overallScore"]))) for row in rows]
    overall_ranks = configured_ranks(overall_inputs)
    activity_ranks = {
        code: configured_ranks([(row["profileKey"], Decimal(str(row["activities"][code]["score"]))) for row in rows])
        for code in ACTIVITY_ORDER
    }
    dimension_ranks = {
        code: configured_ranks([(row["profileKey"], Decimal(str(row["dimensions"][code]["score"]))) for row in rows])
        for code in DIMENSION_ORDER
    }
    for row in rows:
        row["overallRank"] = overall_ranks.get(row["profileKey"])
        for code in ACTIVITY_ORDER:
            row["activities"][code]["rank"] = activity_ranks[code].get(row["profileKey"])
        for code in DIMENSION_ORDER:
            row["dimensions"][code]["rank"] = dimension_ranks[code].get(row["profileKey"])
    rows.sort(key=lambda item: (item["overallRank"] or 10**9, item["name"].casefold(), item["journeyName"].casefold()))

    def average(values: list[float]) -> float:
        return float(sum((Decimal(str(value)) for value in values), Decimal("0")) / Decimal(len(values))) if values else 0.0

    definition = active_assessment_definition()
    return {
        "journeyId": None,
        "formula": "(Willingness + Adaptability + Respect + Intelligence + Application + Physical Ability + General) × 20 / 7",
        "scoreMaximum": float(definition.scoring.officialMaximum),
        "dimensionNames": dict(DIMENSION_NAMES),
        "performanceBands": [item.model_dump(mode="json") for item in definition.scoring.bands],
        "dimensionAverages": {code: average([row["dimensions"][code]["score"] for row in rows]) for code in DIMENSION_ORDER},
        "activityAverages": {code: average([row["activities"][code]["score"] for row in rows]) for code in ACTIVITY_ORDER},
        "rows": rows,
    }


def _completed_view(db: Session) -> dict:
    journeys = list(db.scalars(
        select(Journey).where(Journey.status == "completed").order_by(Journey.event_date.desc(), func.lower(Journey.name))
    ))
    recruits: list[dict] = []
    evaluators: list[dict] = []
    snapshots: list[tuple[Journey, dict]] = []
    for journey in journeys:
        payload = _single_journey_view(db, journey)
        recruits.extend(payload["recruits"])
        evaluators.extend(payload["evaluators"])
        snapshots.append((journey, payload["results"]))
    return {
        "scope": "completed",
        "journey": None,
        "journeys": [serialize_journey(db, journey) for journey in journeys],
        "activities": [{"code": code, "name": RUBRICS[code].name} for code in ACTIVITY_ORDER],
        "recruits": recruits,
        "evaluators": evaluators,
        "results": _aggregate_results(snapshots),
    }


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
    return _single_journey_view(db, journey)


@router.get("/completed")
def completed_view(context: UserContext = Depends(require_results), db: Session = Depends(get_db)):
    del context
    return _completed_view(db)


@router.get("/journeys/{journey_id}/recruits/{recruit_id}/profile")
def profile_view(
    journey_id: str,
    recruit_id: str,
    scope: str = Query(default="journey", pattern="^(journey|completed)$"),
    context: UserContext = Depends(require_results),
    db: Session = Depends(get_db),
):
    del context
    payload = recruit_profile(journey_id, recruit_id, context=None, db=db)
    if scope == "completed":
        journey = get_journey_or_404(db, journey_id)
        if journey.status != "completed":
            raise HTTPException(status_code=404, detail="Recruit is not part of a completed Journee.")
        aggregate = _completed_view(db)
        result = next((item for item in aggregate["results"]["rows"] if item["profileKey"] == f"{journey_id}:{recruit_id}"), None)
        if result is not None:
            payload["result"] = result
        payload["dimensionAverages"] = aggregate["results"]["dimensionAverages"]
        payload["activityAverages"] = aggregate["results"]["activityAverages"]
        for code in DIMENSION_ORDER:
            if result is not None and code in payload.get("dimensionBreakdowns", {}):
                payload["dimensionBreakdowns"][code]["rank"] = result["dimensions"][code]["rank"]
    if payload.get("photoUrl"):
        payload["photoUrl"] = f"/api/view/journeys/{journey_id}/recruits/{recruit_id}/photo"
    payload["journey"] = serialize_journey(db, get_journey_or_404(db, journey_id))
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
def profile_photo(journey_id: str, recruit_id: str, request: Request, context: UserContext = Depends(require_results), db: Session = Depends(get_db)):
    del context
    recruit = db.get(Recruit, recruit_id)
    if not recruit or recruit.journey_id != journey_id or not has_photo(recruit):
        raise HTTPException(status_code=404, detail="Photo not found.")
    data = read_recruit_photo(recruit)
    if not data:
        raise HTTPException(status_code=404, detail="Photo not found.")
    headers = {"Cache-Control": "private, max-age=86400"}
    if recruit.photo_sha256:
        headers["ETag"] = f'"{recruit.photo_sha256}"'
        if request.headers.get("if-none-match") == headers["ETag"]:
            return Response(status_code=304, headers=headers)
    return Response(data, media_type=recruit.photo_type or "image/webp", headers=headers)


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


@router.get("/report.xlsx")
def management_report(
    context: UserContext = Depends(require_results),
    db: Session = Depends(get_db),
):
    del context
    workbook = build_management_report_workbook(db)
    output = io.BytesIO()
    save_management_report(workbook, output)
    return StreamingResponse(
        io.BytesIO(output.getvalue()),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="LRC-Journee-management-report.xlsx"',
            "Cache-Control": "no-store",
        },
    )
