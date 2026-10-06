"""Snapshot-consistent, renderer-neutral management report data.

The loader is the only function in this module that touches SQLAlchemy.  It
copies every value needed by both the Excel report and Google Sheet
presentation while the caller's transaction is alive.  The payload builder is
therefore safe to call after that transaction has closed.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .assessment_runtime import active_assessment_definition
from .general_assessment import configured_general_assessment_values
from .models import (
    AdminEvaluation,
    Assignment,
    AssignmentRound,
    AuditEvent,
    EvaluationSubmission,
    Evaluator,
    GeneralAssessment,
    Journey,
    Recruit,
)
from .rubric import ACTIVITY_ORDER, DIMENSION_NAMES, DIMENSION_ORDER, RUBRICS
from .scoring import configured_ranks
from .services import result_snapshot
from .utils import loads


BEIRUT = ZoneInfo("Asia/Beirut")


def _primitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_primitive(item) for item in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _local_time(value: datetime | str | None) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BEIRUT).strftime("%d %b %Y, %H:%M")


def _label(value: object) -> str:
    return str(value or "").replace("_", " ").replace(".", " › ").title()


@dataclass(frozen=True)
class ManagementReportSource:
    """All report inputs copied to primitive containers inside one snapshot."""

    definition: dict[str, object]
    journeys: tuple[dict[str, object], ...]
    result_snapshots: tuple[dict[str, object], ...]
    combined_results: dict[str, object]


def _definition_payload() -> dict[str, object]:
    definition = active_assessment_definition()
    return _primitive({
        "name": definition.name,
        "terminology": definition.terminology.model_dump(mode="json"),
        "officialMaximum": definition.scoring.officialMaximum,
        "bands": [item.model_dump(mode="json") for item in definition.scoring.bands],
        "dimensions": [
            {
                "key": item.key,
                "name": item.name,
                "displayMaximum": item.displayMaximum,
            }
            for item in definition.dimensions
        ],
        "activities": [
            {
                "key": item.key,
                "name": item.name,
                "criteria": [
                    {
                        "key": criterion.key,
                        "dimension": criterion.dimensionName
                        or DIMENSION_NAMES.get(criterion.dimensionKey, criterion.dimensionKey),
                        "name": criterion.name,
                        "explanation": criterion.explanation,
                        "unit": criterion.unit,
                    }
                    for criterion in item.criteria
                ],
            }
            for item in definition.activities
            if item.enabled
        ],
        "generalFactors": [item.model_dump(mode="json") for item in definition.generalFactors],
    })


def _combined_results(snapshots: list[tuple[dict[str, object], dict[str, object]]]) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for journey, snapshot in snapshots:
        source_rows = snapshot.get("rows", [])
        for source in source_rows:
            row = deepcopy(source)
            row.update({
                "journeyId": journey["id"],
                "journeyName": journey["name"],
                "journeyDate": journey["eventDate"],
                "profileKey": f"{journey['id']}:{row['recruitId']}",
                "journeyRank": row.get("journeyRank", row.get("overallRank")),
                "journeyPopulation": len(source_rows),
            })
            rows.append(row)

    overall = configured_ranks([
        (str(row["profileKey"]), Decimal(str(row.get("overallScore", 0)))) for row in rows
    ])
    dimension_ranks = {
        code: configured_ranks([
            (str(row["profileKey"]), Decimal(str(row["dimensions"][code].get("score", 0))))
            for row in rows
        ])
        for code in DIMENSION_ORDER
    }
    activity_ranks = {
        code: configured_ranks([
            (str(row["profileKey"]), Decimal(str(row["activities"][code].get("score", 0))))
            for row in rows
        ])
        for code in ACTIVITY_ORDER
    }
    for row in rows:
        key = str(row["profileKey"])
        row["overallRank"] = overall.get(key)
        row["overallPopulation"] = len(rows)
        for code in DIMENSION_ORDER:
            row["dimensions"][code]["rank"] = dimension_ranks[code].get(key)
        for code in ACTIVITY_ORDER:
            row["activities"][code]["rank"] = activity_ranks[code].get(key)
    rows.sort(key=lambda row: (
        row.get("overallRank") in (None, ""),
        row.get("overallRank") or 10**9,
        str(row.get("name", "")).casefold(),
        str(row.get("journeyName", "")).casefold(),
        str(row.get("profileKey", "")),
    ))
    return {"rows": rows}


def _load_journey(db: Session, journey: Journey, *, include_criteria: bool) -> tuple[dict[str, object], dict[str, object]]:
    recruits = list(db.scalars(
        select(Recruit)
        .where(Recruit.journey_id == journey.id, Recruit.active.is_(True))
        .order_by(func.lower(Recruit.name), Recruit.id)
    ))
    evaluators = list(db.scalars(
        select(Evaluator)
        .where(Evaluator.journey_id == journey.id, Evaluator.active.is_(True))
        .order_by(func.lower(Evaluator.name), Evaluator.id)
    ))
    rounds = list(db.scalars(select(AssignmentRound).where(AssignmentRound.journey_id == journey.id)))
    round_by_id = {item.id: item for item in rounds}
    assignments = list(db.scalars(
        select(Assignment).where(Assignment.round_id.in_(list(round_by_id)))
    )) if round_by_id else []
    assignment_ids = [item.id for item in assignments]
    submissions = list(db.scalars(
        select(EvaluationSubmission).where(EvaluationSubmission.assignment_id.in_(assignment_ids))
    )) if assignment_ids else []
    admin_evaluations = list(db.scalars(
        select(AdminEvaluation).where(AdminEvaluation.journey_id == journey.id)
    ))
    recruit_ids = [item.id for item in recruits]
    assessments = list(db.scalars(
        select(GeneralAssessment).where(GeneralAssessment.recruit_id.in_(recruit_ids))
    )) if recruit_ids else []
    audits = list(db.scalars(
        select(AuditEvent)
        .where(AuditEvent.journey_id == journey.id, AuditEvent.entity_id.in_(recruit_ids))
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id)
    )) if recruit_ids else []
    snapshot = _primitive(result_snapshot(db, journey, include_criteria=include_criteria))
    journey_row = {
        "id": journey.id,
        "name": journey.name,
        "eventDate": journey.event_date.isoformat(),
        "status": journey.status,
        "recruits": [
            {
                "id": item.id,
                "name": item.name,
                "phoneNumber": item.phone_number or "",
                "dateOfBirth": item.date_of_birth.isoformat() if item.date_of_birth else "",
                "present": bool(item.present),
                "arrivalTime": item.arrival_time.isoformat() if item.arrival_time else "",
                "attendanceComment": item.attendance_comment or "",
                "photoAvailable": bool(item.photo_object_key or item.photo_sha256 or item.photo_size),
            }
            for item in recruits
        ],
        "evaluators": [
            {"id": item.id, "name": item.name, "role": item.role, "present": bool(item.present)}
            for item in evaluators
        ],
        "assignments": [
            {
                "id": item.id,
                "recruitId": item.recruit_id,
                "evaluatorId": item.evaluator_id,
                "activityCode": round_by_id[item.round_id].activity_code if item.round_id in round_by_id else "",
            }
            for item in assignments
        ],
        "submissions": [
            {
                "assignmentId": item.assignment_id,
                "score": float(item.score),
                "status": item.status,
                "comments": item.comments or "",
                "responses": _primitive(loads(item.responses_json, {})),
                "raw": _primitive(loads(item.raw_payload_json, {})),
            }
            for item in submissions
        ],
        "adminEvaluations": [
            {
                "recruitId": item.recruit_id,
                "activityCode": item.activity_code,
                "score": float(item.score),
                "comments": item.comments or "",
                "updatedBy": item.updated_by,
                "responses": _primitive(loads(item.responses_json, {})),
                "raw": _primitive(loads(item.raw_payload_json, {})),
            }
            for item in admin_evaluations
        ],
        "assessments": {
            item.recruit_id: {
                "values": _primitive(configured_general_assessment_values(
                    item, (factor.storageKey for factor in active_assessment_definition().generalFactors)
                )),
                "comment": item.comment or "",
                "notes": item.notes or "",
            }
            for item in assessments
        },
        "audit": [
            {
                "profileKey": f"{journey.id}:{item.entity_id}",
                "createdAt": item.created_at.isoformat(),
                "actorName": item.actor_name,
                "action": item.action,
                "reason": item.reason or "",
                "before": item.before_json or "",
                "after": item.after_json or "",
            }
            for item in audits
        ],
        "results": snapshot,
    }
    return journey_row, snapshot


def load_management_report_source(
    db: Session,
    *,
    include_criteria: bool = True,
) -> ManagementReportSource:
    journeys = list(db.scalars(
        select(Journey)
        .where(Journey.status == "completed")
        .order_by(Journey.event_date.desc(), func.lower(Journey.name), Journey.id)
    ))
    loaded = [_load_journey(db, journey, include_criteria=include_criteria) for journey in journeys]
    journey_rows = tuple(item[0] for item in loaded)
    snapshots = tuple({
        **deepcopy(item[1]),
        "journeyId": item[0]["id"],
        "journeyName": item[0]["name"],
    } for item in loaded)
    combined = _combined_results([(item[0], item[1]) for item in loaded])
    return ManagementReportSource(
        definition=_definition_payload(),
        journeys=journey_rows,
        result_snapshots=snapshots,
        combined_results=_primitive(combined),
    )


def _fallback_result(recruit: dict[str, object], definition: dict[str, object]) -> dict[str, object]:
    activities = definition["activities"]
    dimensions = definition["dimensions"]
    factors = definition["generalFactors"]
    bands = sorted(definition["bands"], key=lambda item: float(item["minimum"]))
    return {
        "recruitId": recruit["id"],
        "name": recruit["name"],
        "overallScore": 0.0,
        "overallRank": None,
        "color": bands[0]["key"] if bands else "",
        "missingCount": len(activities) + len(factors),
        "missingComponents": [item["name"] for item in activities] + [item["name"] for item in factors],
        "dimensions": {
            item["key"]: {"score": 0.0, "rank": None, "complete": False, "availableWeight": 0.0}
            for item in dimensions
        },
        "activities": {
            item["key"]: {"score": 0.0, "rank": None, "complete": False, "submitted": 0, "expected": 0}
            for item in activities
        },
        "generalAverage": 0.0,
        "generalComment": "",
        "notes": "",
        "complete": False,
    }


def _rank_key(row: dict[str, object]) -> tuple[object, ...]:
    rank = row.get("rank")
    return (
        rank in (None, ""),
        rank or 10**9,
        str(row.get("name", "")).casefold(),
        str(row.get("journeyName", "")).casefold(),
        str(row.get("profileKey", "")),
    )


def _results_payload(source: ManagementReportSource) -> dict[str, object]:
    definition = source.definition
    terms = definition["terminology"]
    all_completed = f"All completed {terms['sessionPlural']}"
    dimensions = definition["dimensions"]
    activities = definition["activities"]
    official_maximum = float(definition["officialMaximum"])
    scopes = [all_completed, *[str(item["name"]) for item in source.journeys]]
    views = ["Overall ranking", *[item["name"] for item in dimensions], *[item["name"] for item in activities]]
    rows: list[dict[str, object]] = []

    by_journey = {str(item["journeyId"]): item for item in source.result_snapshots}
    sets: list[tuple[str, list[dict[str, object]]]] = [(all_completed, source.combined_results["rows"])]
    for journey in source.journeys:
        sets.append((str(journey["name"]), by_journey[str(journey["id"])].get("rows", [])))

    dimension_by_key = {item["key"]: item for item in dimensions}
    activity_by_key = {item["key"]: item for item in activities}
    for scope, result_rows in sets:
        for item in result_rows:
            journey_name = str(item.get("journeyName") or scope)
            profile_key = str(item.get("profileKey") or f"{next(j['id'] for j in source.journeys if j['name'] == scope)}:{item['recruitId']}")
            rows.append({
                "scope": scope, "view": "Overall ranking", "rank": item.get("overallRank"),
                "name": item["name"], "journeyName": journey_name, "profileKey": profile_key,
                "score": item.get("overallScore", 0), "scale": f"/{official_maximum:g}",
                "details": f"{item.get('missingCount', 0)} missing",
                "status": "Complete" if item.get("complete") else "Incomplete",
                "color": str(item.get("colorGrade", item.get("color", ""))).title(),
                "generalComment": item.get("generalComment", ""), "notes": item.get("notes", ""),
            })
            for code in DIMENSION_ORDER:
                value = item["dimensions"][code]
                config = dimension_by_key[code]
                maximum = float(config["displayMaximum"])
                rows.append({
                    "scope": scope, "view": config["name"], "rank": value.get("rank"),
                    "name": item["name"], "journeyName": journey_name, "profileKey": profile_key,
                    "score": float(value.get("score", 0)) * maximum, "scale": f"/{maximum:g}",
                    "details": f"{round(float(value.get('availableWeight', 0)) * 100)}% coverage",
                    "status": "Complete" if value.get("complete") else "Incomplete",
                    "color": "", "generalComment": "", "notes": "",
                })
            for code in ACTIVITY_ORDER:
                value = item["activities"][code]
                rows.append({
                    "scope": scope, "view": activity_by_key[code]["name"], "rank": value.get("rank"),
                    "name": item["name"], "journeyName": journey_name, "profileKey": profile_key,
                    "score": value.get("score", 0), "scale": "/5",
                    "details": f"{value.get('submitted', 0)}/{value.get('expected', 0)} submitted",
                    "status": "Complete" if value.get("complete") else "Incomplete",
                    "color": "", "generalComment": "", "notes": "",
                })
    rows.sort(key=lambda row: (scopes.index(row["scope"]), views.index(row["view"]), *_rank_key(row)))
    return {"scopes": scopes, "views": views, "rows": rows}


def _profile_labels(source: ManagementReportSource) -> dict[str, str]:
    items: list[tuple[str, str, str, str, str]] = []
    for journey in source.journeys:
        for recruit in journey["recruits"]:
            items.append((
                str(recruit["name"]).strip(), str(journey["eventDate"]), str(journey["name"]),
                str(journey["id"]), str(recruit["id"]),
            ))
    items.sort(key=lambda item: (item[0].casefold(), item[1], item[2].casefold(), item[3], item[4]))
    totals = Counter(item[0].casefold() for item in items)
    seen: defaultdict[str, int] = defaultdict(int)
    labels: dict[str, str] = {}
    for name, _event_date, journey_name, journey_id, recruit_id in items:
        key = name.casefold()
        seen[key] += 1
        labels[f"{journey_id}:{recruit_id}"] = (
            name if totals[key] == 1 else f"{name} — {journey_name} · {seen[key]}"
        )
    return labels


def _profiles_payload(source: ManagementReportSource) -> dict[str, object]:
    definition = source.definition
    terms = definition["terminology"]
    all_completed = f"All completed {terms['sessionPlural']}"
    scopes = [all_completed, *[str(item["name"]) for item in source.journeys]]
    labels = _profile_labels(source)
    combined_by_key = {str(row["profileKey"]): row for row in source.combined_results["rows"]}
    snapshots = {str(item["journeyId"]): item for item in source.result_snapshots}
    dimension_config = {item["key"]: item for item in definition["dimensions"]}
    activity_config = {item["key"]: item for item in definition["activities"]}
    options_by_scope: dict[str, list[dict[str, object]]] = {scope: [] for scope in scopes}
    summaries: list[dict[str, object]] = []
    dimensions: list[dict[str, object]] = []
    activities: list[dict[str, object]] = []
    evaluators: list[dict[str, object]] = []
    criteria: list[dict[str, object]] = []
    audit: list[dict[str, object]] = []

    for journey in source.journeys:
        journey_id = str(journey["id"])
        journey_name = str(journey["name"])
        result_rows = snapshots[journey_id].get("rows", [])
        journey_results = {str(item["recruitId"]): item for item in result_rows}
        evaluator_by_id = {str(item["id"]): item for item in journey["evaluators"]}
        submission_by_assignment = {str(item["assignmentId"]): item for item in journey["submissions"]}
        assessments = journey["assessments"]
        journey_population = len(result_rows)

        for recruit in journey["recruits"]:
            recruit_id = str(recruit["id"])
            profile_key = f"{journey_id}:{recruit_id}"
            label = labels[profile_key]
            option = {
                "profileKey": profile_key, "label": label, "name": recruit["name"],
                "journeyId": journey_id, "journeyName": journey_name,
            }
            options_by_scope[all_completed].append(deepcopy(option))
            options_by_scope[journey_name].append(deepcopy(option))
            journey_result = journey_results.get(recruit_id) or _fallback_result(recruit, definition)
            combined_result = combined_by_key.get(profile_key, journey_result)
            assessment = assessments.get(recruit_id, {"values": {}, "comment": "", "notes": ""})

            for scope, displayed_result in ((all_completed, combined_result), (journey_name, journey_result)):
                selection_key = f"{scope}|{label}"
                summary = {
                    "selectionKey": selection_key,
                    "scope": scope,
                    "profileKey": profile_key,
                    "label": label,
                    "journeyId": journey_id,
                    "journeyName": journey_name,
                    "journeyDate": journey["eventDate"],
                    "name": recruit["name"],
                    "phoneNumber": recruit["phoneNumber"],
                    "dateOfBirth": recruit["dateOfBirth"],
                    "attendance": "Present" if recruit["present"] else "Absent",
                    "arrivalTime": _local_time(recruit["arrivalTime"]),
                    "attendanceComment": recruit["attendanceComment"],
                    "overallScore": displayed_result.get("overallScore", 0),
                    "displayRank": displayed_result.get("overallRank"),
                    "overallRank": combined_result.get("overallRank") if profile_key in combined_by_key else None,
                    "overallPopulation": combined_result.get("overallPopulation", len(source.combined_results["rows"])),
                    "journeyRank": journey_result.get("journeyRank", journey_result.get("overallRank")),
                    "journeyPopulation": journey_population,
                    "color": str(displayed_result.get("colorGrade", displayed_result.get("color", ""))).title(),
                    "missingComponents": ", ".join(displayed_result.get("missingComponents", [])) or "Complete",
                    "generalValues": {
                        item["storageKey"]: assessment["values"].get(item["storageKey"])
                        for item in definition["generalFactors"]
                    },
                    "generalAverage": displayed_result.get("generalAverage", 0),
                    "generalComment": assessment.get("comment", ""),
                    "notes": assessment.get("notes", ""),
                    "photoAvailable": recruit["photoAvailable"],
                }
                summaries.append(summary)
                for code in DIMENSION_ORDER:
                    value = displayed_result["dimensions"][code]
                    maximum = float(dimension_config[code]["displayMaximum"])
                    dimensions.append({
                        "selectionKey": selection_key, "profileKey": profile_key,
                        "name": dimension_config[code]["name"],
                        "score": float(value.get("score", 0)) * maximum,
                        "rank": value.get("rank"),
                        "status": "Complete" if value.get("complete") else "Incomplete",
                        "coverage": f"{round(float(value.get('availableWeight', 0)) * 100)}%",
                    })
                for code in ACTIVITY_ORDER:
                    value = displayed_result["activities"][code]
                    activities.append({
                        "selectionKey": selection_key, "profileKey": profile_key,
                        "name": activity_config[code]["name"], "score": value.get("score", 0),
                        "rank": value.get("rank"),
                        "submissions": f"{value.get('submitted', 0)}/{value.get('expected', 0)}",
                        "status": "Complete" if value.get("complete") else "Incomplete",
                    })

            for assignment in journey["assignments"]:
                if assignment["recruitId"] != recruit_id:
                    continue
                code = str(assignment["activityCode"])
                rubric = RUBRICS.get(code)
                evaluator = evaluator_by_id.get(str(assignment["evaluatorId"]))
                submission = submission_by_assignment.get(str(assignment["id"]))
                evaluators.append({
                    "profileKey": profile_key,
                    "activity": rubric.name if rubric else code,
                    "evaluator": evaluator["name"] if evaluator else "Unknown",
                    "category": str(evaluator["role"]).title() if evaluator else "",
                    "score": submission["score"] if submission else "",
                    "status": _label(submission["status"]) if submission else "Missing",
                    "comment": submission["comments"] if submission else "",
                })
                if include := (rubric and submission):
                    for criterion in rubric.criteria:
                        raw_value = submission["raw"].get(criterion.key, "")
                        criteria.append({
                            "profileKey": profile_key, "activity": rubric.name,
                            "dimension": criterion.dimension, "criterion": criterion.name,
                            "explanation": criterion.explanation,
                            "evaluator": evaluator["name"] if evaluator else "Unknown",
                            "grade": submission["responses"].get(criterion.key, ""),
                            "rawResult": f"{raw_value} {criterion.unit}".strip() if raw_value != "" else "",
                            "status": _label(submission["status"]),
                        })
            for evaluation in journey["adminEvaluations"]:
                if evaluation["recruitId"] != recruit_id:
                    continue
                code = str(evaluation["activityCode"])
                rubric = RUBRICS.get(code)
                evaluators.append({
                    "profileKey": profile_key, "activity": rubric.name if rubric else code,
                    "evaluator": f"Admin: {evaluation['updatedBy']}", "category": "Admin",
                    "score": evaluation["score"], "status": "Official", "comment": evaluation["comments"],
                })
                if rubric:
                    for criterion in rubric.criteria:
                        raw_value = evaluation["raw"].get(criterion.key, "")
                        criteria.append({
                            "profileKey": profile_key, "activity": rubric.name,
                            "dimension": criterion.dimension, "criterion": criterion.name,
                            "explanation": criterion.explanation,
                            "evaluator": f"Admin: {evaluation['updatedBy']}",
                            "grade": evaluation["responses"].get(criterion.key, ""),
                            "rawResult": f"{raw_value} {criterion.unit}".strip() if raw_value != "" else "",
                            "status": "Official admin evaluation",
                        })
        audit.extend(deepcopy(journey["audit"]))

    for values in options_by_scope.values():
        values.sort(key=lambda item: (str(item["label"]).casefold(), str(item["profileKey"])))
    summaries.sort(key=lambda row: (scopes.index(str(row["scope"])), str(row["label"]).casefold(), str(row["profileKey"])))
    dimensions.sort(key=lambda row: (str(row["selectionKey"]), str(row["name"])))
    activities.sort(key=lambda row: (str(row["selectionKey"]), str(row["name"])))
    evaluators.sort(key=lambda row: (str(row["profileKey"]), str(row["activity"]), str(row["evaluator"])))
    criteria.sort(key=lambda row: (str(row["profileKey"]), str(row["activity"]), str(row["criterion"]), str(row["evaluator"])))
    audit.sort(key=lambda row: (str(row["profileKey"]), str(row["createdAt"])), reverse=True)
    default_options = options_by_scope[all_completed]
    return {
        "scopes": scopes,
        "optionsByScope": options_by_scope,
        "defaultScope": all_completed,
        "defaultProfileKey": default_options[0]["profileKey"] if default_options else "",
        "defaultLabel": default_options[0]["label"] if default_options else "",
        "summaries": summaries,
        "dimensions": dimensions,
        "activities": activities,
        "evaluators": evaluators,
        "criteria": criteria,
        "audit": audit,
        "generalFactors": deepcopy(definition["generalFactors"]),
        "dimensionDefinitions": deepcopy(definition["dimensions"]),
        "activityDefinitions": deepcopy(definition["activities"]),
    }


def _attendance_payload(source: ManagementReportSource) -> dict[str, object]:
    terms = source.definition["terminology"]
    all_completed = f"All completed {terms['sessionPlural']}"
    scopes = [all_completed, *[str(item["name"]) for item in source.journeys]]
    rows: list[dict[str, object]] = []
    for journey in source.journeys:
        for recruit in journey["recruits"]:
            row = {
                "journeyName": journey["name"], "name": recruit["name"],
                "phoneNumber": recruit["phoneNumber"], "dateOfBirth": recruit["dateOfBirth"],
                "status": "Present" if recruit["present"] else "Absent",
                "arrivalTime": _local_time(recruit["arrivalTime"]),
                "attendanceComment": recruit["attendanceComment"],
                "profileKey": f"{journey['id']}:{recruit['id']}",
            }
            for scope in (all_completed, journey["name"]):
                rows.append({"scope": scope, **row})
    rows.sort(key=lambda row: (scopes.index(str(row["scope"])), str(row["name"]).casefold(), str(row["profileKey"])))
    return {"scopes": scopes, "rows": rows}


def build_management_report_payload(source: ManagementReportSource) -> dict[str, object]:
    """Build the shared JSON-safe payload without querying DB or object storage."""

    payload = {
        "definition": deepcopy(source.definition),
        "attendance": _attendance_payload(source),
        "results": _results_payload(source),
        "profiles": _profiles_payload(source),
        "rawResults": {
            "snapshots": deepcopy(list(source.result_snapshots)),
            "combined": deepcopy(source.combined_results),
        },
    }
    return _primitive(payload)
