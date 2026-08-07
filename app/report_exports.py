from __future__ import annotations

import io
import re
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.chart import RadarChart, Reference
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from PIL import Image as PillowImage
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    ActivityState,
    AdminEvaluation,
    Assignment,
    AssignmentRound,
    AuditEvent,
    EvaluationSubmission,
    Evaluator,
    GeneralAssessment,
    Journey,
    Recruit,
    RoomPlanEvaluator,
    RoomPlanRecruit,
)
from .rubric import ACTIVITY_ORDER, DIMENSION_NAMES, DIMENSION_ORDER, RUBRICS
from .services import latest_room_plan, result_snapshot
from .utils import loads


BEIRUT = ZoneInfo("Asia/Beirut")
NAVY = "223449"
RED = "C8102E"
RED_LIGHT = "FCE9ED"
BLUE_LIGHT = "EAF1F8"
GREEN = "16834B"
GREEN_LIGHT = "E8F5ED"
YELLOW = "E3AD22"
YELLOW_LIGHT = "FFF5D9"
GRAY = "667085"
GRAY_LIGHT = "F3F5F7"
LINE = "D6DBE1"
WHITE = "FFFFFF"
THIN_LINE = Side(style="thin", color=LINE)


def _label(value: object) -> str:
    return str(value or "").replace("_", " ").replace(".", " › ").title()


def _local_time(value: datetime | None) -> str:
    if not value:
        return "Not recorded"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BEIRUT).strftime("%d %b %Y, %H:%M")


def _new_sheet(workbook: Workbook, title: str, *, landscape: bool = False):
    sheet = workbook.create_sheet(title=title)
    sheet.sheet_view.showGridLines = False
    sheet.sheet_view.zoomScale = 90
    sheet.freeze_panes = "A4"
    sheet.page_setup.orientation = "landscape" if landscape else "portrait"
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.oddFooter.center.text = "LRC Journee Recruitment · View-only export"
    sheet.oddFooter.right.text = "Page &P of &N"
    return sheet


def _title(sheet, title: str, subtitle: str, span: int = 10) -> None:
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=span)
    cell = sheet.cell(1, 1, title)
    cell.fill = PatternFill("solid", fgColor=NAVY)
    cell.font = Font(name="Aptos Display", size=18, bold=True, color=WHITE)
    cell.alignment = Alignment(vertical="center")
    sheet.row_dimensions[1].height = 34
    sheet.merge_cells(start_row=2, start_column=1, end_row=2, end_column=span)
    sub = sheet.cell(2, 1, subtitle)
    sub.font = Font(name="Aptos", size=10, color=GRAY)
    sub.alignment = Alignment(vertical="center")
    sheet.row_dimensions[2].height = 24


def _section(sheet, row: int, title: str, span: int = 10) -> int:
    sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=span)
    cell = sheet.cell(row, 1, title)
    cell.fill = PatternFill("solid", fgColor=RED_LIGHT)
    cell.font = Font(name="Aptos", size=11, bold=True, color=RED)
    cell.alignment = Alignment(vertical="center")
    sheet.row_dimensions[row].height = 25
    return row + 1


def _write_table(
    sheet,
    row: int,
    headers: list[str],
    rows: list[list],
    *,
    widths: list[float] | None = None,
    number_formats: dict[int, str] | None = None,
    auto_filter: bool = False,
) -> int:
    header_row = row
    for column, header in enumerate(headers, 1):
        cell = sheet.cell(row, column, header)
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.font = Font(name="Aptos", size=9, bold=True, color=WHITE)
        cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    sheet.row_dimensions[row].height = 28
    for values in rows:
        row += 1
        for column, value in enumerate(values, 1):
            cell = sheet.cell(row, column, value)
            cell.font = Font(name="Aptos", size=9, color="20252B")
            cell.alignment = Alignment(vertical="top", wrap_text=isinstance(value, str) and len(value) > 32)
            cell.border = Border(bottom=THIN_LINE)
            if row % 2 == 0:
                cell.fill = PatternFill("solid", fgColor="FAFBFC")
            if number_formats and column in number_formats and value not in (None, ""):
                cell.number_format = number_formats[column]
        sheet.row_dimensions[row].height = 22
    if auto_filter and rows:
        sheet.auto_filter.ref = f"A{header_row}:{get_column_letter(len(headers))}{row}"
    if widths:
        for column, width in enumerate(widths, 1):
            sheet.column_dimensions[get_column_letter(column)].width = width
    return row + 2


def _status_fill(cell, status: str) -> None:
    status = status.casefold()
    if status in {"complete", "completed", "present", "submitted", "locked", "green", "active", "open"}:
        cell.fill = PatternFill("solid", fgColor=GREEN_LIGHT)
        cell.font = Font(name="Aptos", size=9, bold=True, color=GREEN)
    elif status in {"yellow", "incomplete", "draft", "ready", "not started", "not_started"}:
        cell.fill = PatternFill("solid", fgColor=YELLOW_LIGHT)
        cell.font = Font(name="Aptos", size=9, bold=True, color="745300")
    elif status in {"red", "absent", "missing", "archived", "closed"}:
        cell.fill = PatternFill("solid", fgColor=RED_LIGHT)
        cell.font = Font(name="Aptos", size=9, bold=True, color=RED)


def _add_radar(sheet, start_row: int, end_row: int, category_column: int, value_column: int, anchor: str, title: str, maximum: float) -> None:
    chart = RadarChart()
    data = Reference(sheet, min_col=value_column, min_row=start_row - 1, max_row=end_row)
    categories = Reference(sheet, min_col=category_column, min_row=start_row, max_row=end_row)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(categories)
    chart.title = title
    chart.style = 26
    chart.height = 7.2
    chart.width = 10.5
    chart.legend = None
    chart.y_axis.scaling.min = 0
    chart.y_axis.scaling.max = maximum
    sheet.add_chart(chart, anchor)


def _safe_profile_title(workbook: Workbook, index: int, name: str) -> str:
    base = re.sub(r"[\\/*?:\[\]]", "-", f"Profile {index:02d} - {name}")[:31].strip()
    title = base
    suffix = 2
    while title in workbook.sheetnames:
        marker = f" {suffix}"
        title = (base[: 31 - len(marker)] + marker).strip()
        suffix += 1
    return title


def _collect(db: Session, journey: Journey) -> dict:
    recruits = list(db.scalars(select(Recruit).where(Recruit.journey_id == journey.id, Recruit.active.is_(True)).order_by(Recruit.name)))
    evaluators = list(db.scalars(select(Evaluator).where(Evaluator.journey_id == journey.id, Evaluator.active.is_(True)).order_by(Evaluator.name)))
    recruit_by_id = {item.id: item for item in recruits}
    evaluator_by_id = {item.id: item for item in evaluators}
    states = {item.code: item for item in db.scalars(select(ActivityState).where(ActivityState.journey_id == journey.id))}
    round_ids = [item.assignment_round_id for item in states.values() if item.assignment_round_id]
    rounds = {item.id: item for item in db.scalars(select(AssignmentRound).where(AssignmentRound.id.in_(round_ids)))} if round_ids else {}
    assignments = list(db.scalars(select(Assignment).where(Assignment.round_id.in_(round_ids)))) if round_ids else []
    assignment_by_id = {item.id: item for item in assignments}
    assignment_activity = {item.id: rounds[item.round_id].activity_code for item in assignments if item.round_id in rounds}
    submission_rows = list(db.scalars(select(EvaluationSubmission).where(EvaluationSubmission.assignment_id.in_(list(assignment_by_id))))) if assignments else []
    submissions = {item.assignment_id: item for item in submission_rows}
    admin_evaluations = list(db.scalars(select(AdminEvaluation).where(AdminEvaluation.journey_id == journey.id)))
    assessments = {item.recruit_id: item for item in db.scalars(select(GeneralAssessment).where(GeneralAssessment.recruit_id.in_(list(recruit_by_id))))} if recruits else {}
    results = result_snapshot(db, journey)
    result_by_recruit = {item["recruitId"]: item for item in results["rows"]}
    return {
        "recruits": recruits,
        "evaluators": evaluators,
        "recruit_by_id": recruit_by_id,
        "evaluator_by_id": evaluator_by_id,
        "states": states,
        "rounds": rounds,
        "assignments": assignments,
        "assignment_activity": assignment_activity,
        "submissions": submissions,
        "admin_evaluations": admin_evaluations,
        "assessments": assessments,
        "results": results,
        "result_by_recruit": result_by_recruit,
    }


def _dashboard_sheet(workbook: Workbook, journey: Journey, data: dict) -> None:
    sheet = _new_sheet(workbook, "Journee Dashboard", landscape=True)
    _title(sheet, journey.name, f"{journey.event_date:%d %B %Y} · {_label(journey.status)} · Exported {_local_time(datetime.now(timezone.utc))}", 12)
    recruits = data["recruits"]
    evaluators = data["evaluators"]
    metrics = [
        ("Present recruits", sum(item.present for item in recruits), len(recruits)),
        ("Present evaluators", sum(item.present for item in evaluators), len(evaluators)),
        ("Overall evaluators", sum(item.present and item.role == "overall" for item in evaluators), "present"),
        ("Dossard evaluators", sum(item.present and item.role == "dossard" for item in evaluators), "present"),
    ]
    for index, (label, value, context) in enumerate(metrics):
        column = 1 + index * 3
        sheet.merge_cells(start_row=4, start_column=column, end_row=4, end_column=column + 1)
        sheet.merge_cells(start_row=5, start_column=column, end_row=6, end_column=column + 1)
        sheet.cell(4, column, label).fill = PatternFill("solid", fgColor=BLUE_LIGHT)
        sheet.cell(4, column).font = Font(name="Aptos", size=9, bold=True, color=NAVY)
        metric_text = f"{value} of {context} present" if isinstance(context, int) else f"{value} {context}"
        sheet.cell(5, column, metric_text).font = Font(name="Aptos Display", size=15, bold=True, color=RED)
        sheet.cell(5, column).alignment = Alignment(vertical="center")
    row = _section(sheet, 8, "Activity lifecycle and completion", 12)
    activity_rows = []
    for code in ACTIVITY_ORDER:
        state = data["states"].get(code)
        assignments = [item for item in data["assignments"] if data["assignment_activity"].get(item.id) == code]
        submitted = sum(item.id in data["submissions"] and data["submissions"][item.id].status in {"submitted", "locked"} for item in assignments)
        activity_rows.append([RUBRICS[code].name, _label(state.status if state else "not_started"), len(assignments), submitted, len(assignments) - submitted, data["results"]["activityAverages"].get(code, 0)])
    row = _write_table(sheet, row, ["Activity", "Status", "Expected evaluations", "Received", "Missing", "Average /5"], activity_rows, widths=[24, 16, 20, 13, 13, 14], number_formats={6: "0.00"})
    for item_row in range(10, 10 + len(activity_rows)):
        _status_fill(sheet.cell(item_row, 2), str(sheet.cell(item_row, 2).value))
    row = _section(sheet, row, "Dimension and activity averages", 12)
    averages = [[DIMENSION_NAMES[code], data["results"]["dimensionAverages"].get(code, 0), "Dimension /1"] for code in DIMENSION_ORDER]
    averages.extend([[RUBRICS[code].name, data["results"]["activityAverages"].get(code, 0), "Activity /5"] for code in ACTIVITY_ORDER])
    row = _write_table(sheet, row, ["Measure", "Average", "Scale"], averages, widths=[28, 14, 16], number_formats={2: "0.00"})
    row = _section(sheet, row, "Provisional overall ranking", 12)
    ranking_rows = [[item["overallRank"], item["name"], item["overallScore"], item["color"].title(), item["missingCount"]] for item in data["results"]["rows"]]
    _write_table(sheet, row, ["Rank", "Recruit", "Overall /20", "Color", "Missing components"], ranking_rows, widths=[10, 30, 16, 13, 20], number_formats={3: "0.00"}, auto_filter=True)


def _attendance_sheets(workbook: Workbook, journey: Journey, data: dict) -> None:
    recruits = _new_sheet(workbook, "Recruit Attendance", landscape=True)
    _title(recruits, "Recruit Attendance", journey.name, 8)
    rows = [[item.name, "Present" if item.present else "Absent", _local_time(item.arrival_time), item.attendance_comment or "", item.phone_number or "", item.date_of_birth, "Yes" if item.photo_data else "No"] for item in data["recruits"]]
    _write_table(recruits, 4, ["Recruit", "Attendance", "Time of arrival (Beirut)", "Attendance comment", "Phone number", "Date of birth", "Photo"], rows, widths=[30, 14, 25, 30, 18, 16, 10], auto_filter=True)
    for row in range(5, 5 + len(rows)):
        _status_fill(recruits.cell(row, 2), str(recruits.cell(row, 2).value))
        recruits.cell(row, 6).number_format = "dd mmm yyyy"

    evaluators = _new_sheet(workbook, "Evaluator Attendance")
    _title(evaluators, "Evaluator Attendance", journey.name, 6)
    rows = [[item.name, item.role.title(), "Present" if item.present else "Absent"] for item in sorted(data["evaluators"], key=lambda item: (not item.present, item.role != "overall", item.name.casefold()))]
    _write_table(evaluators, 4, ["Evaluator", "Role", "Attendance"], rows, widths=[32, 16, 16], auto_filter=True)
    for row in range(5, 5 + len(rows)):
        _status_fill(evaluators.cell(row, 3), str(evaluators.cell(row, 3).value))


def _rooms_sheet(workbook: Workbook, journey: Journey, data: dict) -> None:
    sheet = _new_sheet(workbook, "Rooms", landscape=True)
    _title(sheet, "Published Room Distribution", journey.name, 8)
    plan = latest_room_plan(data["db"], journey.id, "published")
    if not plan:
        sheet.cell(4, 1, "No published room distribution.")
        return
    recruits_by_room: dict[int, list[str]] = defaultdict(list)
    evaluators_by_room: dict[int, list[str]] = defaultdict(list)
    for member in data["db"].scalars(select(RoomPlanRecruit).where(RoomPlanRecruit.plan_id == plan.id)):
        recruit = data["recruit_by_id"].get(member.recruit_id)
        recruits_by_room[member.room_number].append(recruit.name if recruit else "Unknown")
    for member in data["db"].scalars(select(RoomPlanEvaluator).where(RoomPlanEvaluator.plan_id == plan.id)):
        evaluator = data["evaluator_by_id"].get(member.evaluator_id)
        label = f"{evaluator.name if evaluator else 'Unknown'} ({(evaluator.role if evaluator else '').title()})"
        if member.mandatory:
            label += " · Mandatory"
        evaluators_by_room[member.room_number].append(label)
    row = 4
    room_count = max([journey.room_count, *recruits_by_room.keys(), *evaluators_by_room.keys()])
    for room in range(1, room_count + 1):
        row = _section(sheet, row, f"Room {room}", 8)
        maximum = max(len(recruits_by_room[room]), len(evaluators_by_room[room]), 1)
        room_rows = [[recruits_by_room[room][index] if index < len(recruits_by_room[room]) else "", evaluators_by_room[room][index] if index < len(evaluators_by_room[room]) else ""] for index in range(maximum)]
        row = _write_table(sheet, row, ["Recruits", "Evaluators"], room_rows, widths=[34, 42])


def _assignments_sheet(workbook: Workbook, journey: Journey, data: dict) -> None:
    sheet = _new_sheet(workbook, "Assignments", landscape=True)
    _title(sheet, "Current Published Assignments", journey.name, 8)
    rows = []
    for assignment in sorted(data["assignments"], key=lambda item: (ACTIVITY_ORDER.index(data["assignment_activity"].get(item.id, "sport")), item.room_number or 0, data["recruit_by_id"].get(item.recruit_id).name.casefold() if data["recruit_by_id"].get(item.recruit_id) else "")):
        code = data["assignment_activity"].get(assignment.id)
        recruit = data["recruit_by_id"].get(assignment.recruit_id)
        evaluator = data["evaluator_by_id"].get(assignment.evaluator_id)
        submission = data["submissions"].get(assignment.id)
        rows.append([RUBRICS[code].name if code else "Unknown", assignment.room_number or "—", recruit.name if recruit else "Unknown", evaluator.name if evaluator else "Unknown", evaluator.role.title() if evaluator else "", assignment.slot, _label(submission.status) if submission else "Missing", float(submission.score) if submission else ""])
    _write_table(sheet, 4, ["Activity", "Room", "Recruit", "Evaluator", "Role", "Slot", "Evaluation status", "Score /5"], rows, widths=[20, 9, 28, 28, 13, 9, 18, 13], number_formats={8: "0.00"}, auto_filter=True)


def _evaluation_sheet(workbook: Workbook, journey: Journey, data: dict) -> None:
    sheet = _new_sheet(workbook, "Evaluation Details", landscape=True)
    _title(sheet, "Evaluation Details", f"Human-readable criterion report · {journey.name}", 10)
    rows = []
    for assignment in data["assignments"]:
        submission = data["submissions"].get(assignment.id)
        if not submission:
            continue
        code = data["assignment_activity"].get(assignment.id)
        recruit = data["recruit_by_id"].get(assignment.recruit_id)
        evaluator = data["evaluator_by_id"].get(assignment.evaluator_id)
        responses = loads(submission.responses_json, {})
        raw = loads(submission.raw_payload_json, {})
        for criterion in RUBRICS[code].criteria:
            grade = responses.get(criterion.key)
            result = raw.get(criterion.key, "")
            rows.append([RUBRICS[code].name, recruit.name if recruit else "Unknown", evaluator.name if evaluator else "Unknown", evaluator.role.title() if evaluator else "", _label(submission.status), float(submission.score), criterion.dimension, criterion.name, grade if grade is not None else "", f"{result} {criterion.unit}".strip() if result != "" else "", submission.comments])
    for evaluation in data["admin_evaluations"]:
        recruit = data["recruit_by_id"].get(evaluation.recruit_id)
        responses = loads(evaluation.responses_json, {})
        raw = loads(evaluation.raw_payload_json, {})
        for criterion in RUBRICS[evaluation.activity_code].criteria:
            result = raw.get(criterion.key, "")
            rows.append([RUBRICS[evaluation.activity_code].name, recruit.name if recruit else "Unknown", evaluation.updated_by, "Admin", "Official admin evaluation", float(evaluation.score), criterion.dimension, criterion.name, responses.get(criterion.key, ""), f"{result} {criterion.unit}".strip() if result != "" else "", evaluation.comments])
    _write_table(sheet, 4, ["Activity", "Recruit", "Evaluator", "Role", "Status", "Evaluation /5", "Dimension", "Criterion", "Grade /5", "Sport result", "Comment"], rows, widths=[18, 26, 26, 12, 14, 15, 18, 38, 12, 16, 42], number_formats={6: "0.00", 9: "0.00"}, auto_filter=True)


def _rubric_sheet(workbook: Workbook, journey: Journey) -> None:
    sheet = _new_sheet(workbook, "Rubric Guide", landscape=True)
    _title(sheet, "2026 Evaluation Rubric", f"Read-only scoring reference · {journey.name}", 9)
    rows = []
    for code in ACTIVITY_ORDER:
        for criterion in RUBRICS[code].criteria:
            rows.append([RUBRICS[code].name, criterion.dimension, criterion.name, float(criterion.weight), criterion.explanation, _label(criterion.input_type), float(criterion.target) if criterion.target is not None else "", criterion.unit])
    _write_table(sheet, 4, ["Activity", "Dimension / theme", "Criterion", "Activity weight", "Explanation", "Input", "Target", "Unit"], rows, widths=[18, 20, 36, 16, 60, 14, 12, 14], number_formats={4: "0%"}, auto_filter=True)


def _results_sheets(workbook: Workbook, journey: Journey, data: dict) -> None:
    summary = _new_sheet(workbook, "Results Summary", landscape=True)
    _title(summary, "Overall Results & Rankings", f"{journey.name} · {data['results']['formula']}", 16)
    headers = ["Rank", "Recruit", "Overall /20", "Color", "Missing", *[DIMENSION_NAMES[code] + " /1" for code in DIMENSION_ORDER], "General /1", *[RUBRICS[code].name + " /5" for code in ACTIVITY_ORDER]]
    rows = [[item["overallRank"], item["name"], item["overallScore"], item["color"].title(), item["missingCount"], *[item["dimensions"][code]["score"] for code in DIMENSION_ORDER], item["generalAverage"], *[item["activities"][code]["score"] for code in ACTIVITY_ORDER]] for item in data["results"]["rows"]]
    _write_table(summary, 4, headers, rows, widths=[8, 27, 14, 11, 10, *([15] * len(DIMENSION_ORDER)), 13, *([14] * len(ACTIVITY_ORDER))], number_formats={column: "0.00" for column in range(3, len(headers) + 1) if column not in {4, 5}}, auto_filter=True)
    for row in range(5, 5 + len(rows)):
        _status_fill(summary.cell(row, 4), str(summary.cell(row, 4).value))

    dimensions = _new_sheet(workbook, "Dimension Rankings", landscape=True)
    _title(dimensions, "Dimension Rankings", journey.name, 7)
    row = 4
    for code in DIMENSION_ORDER:
        row = _section(dimensions, row, f"{DIMENSION_NAMES[code]} · Average {data['results']['dimensionAverages'].get(code, 0):.2f} /1", 7)
        ranking = sorted(data["results"]["rows"], key=lambda item: (item["dimensions"][code]["rank"] or 10**9, item["name"]))
        rows = [[item["dimensions"][code]["rank"], item["name"], item["dimensions"][code]["score"], item["dimensions"][code]["availableWeight"], "Complete" if item["dimensions"][code]["complete"] else "Incomplete"] for item in ranking]
        row = _write_table(dimensions, row, ["Rank", "Recruit", "Grade /1", "Coverage", "Status"], rows, widths=[9, 30, 14, 13, 15], number_formats={3: "0.00", 4: "0%"})

    activities = _new_sheet(workbook, "Activity Rankings", landscape=True)
    _title(activities, "Activity Rankings", journey.name, 7)
    row = 4
    for code in ACTIVITY_ORDER:
        row = _section(activities, row, f"{RUBRICS[code].name} · Average {data['results']['activityAverages'].get(code, 0):.2f} /5", 7)
        ranking = sorted(data["results"]["rows"], key=lambda item: (item["activities"][code]["rank"] or 10**9, item["name"]))
        rows = [[item["activities"][code]["rank"], item["name"], item["activities"][code]["score"], item["activities"][code]["submitted"], item["activities"][code]["expected"], "Complete" if item["activities"][code]["complete"] else "Incomplete"] for item in ranking]
        row = _write_table(activities, row, ["Rank", "Recruit", "Grade /5", "Submitted", "Expected", "Status"], rows, widths=[9, 30, 14, 13, 13, 15], number_formats={3: "0.00"})


def _profile_sheet(workbook: Workbook, journey: Journey, data: dict, recruit: Recruit, index: int) -> None:
    title = _safe_profile_title(workbook, index, recruit.name)
    sheet = _new_sheet(workbook, title, landscape=True)
    result = data["result_by_recruit"].get(recruit.id)
    assessment = data["assessments"].get(recruit.id)
    color = (result or {}).get("color", "red")
    _title(sheet, recruit.name, f"Recruit profile · {journey.name}", 12)
    details = [
        ["Attendance", "Present" if recruit.present else "Absent", "Arrival", _local_time(recruit.arrival_time)],
        ["Phone", recruit.phone_number or "Not recorded", "Date of birth", recruit.date_of_birth or "Not recorded"],
        ["Overall /20", (result or {}).get("overallScore", 0), "Overall rank", (result or {}).get("overallRank", "—")],
        ["Color grade", color.title(), "Missing components", (result or {}).get("missingCount", 9)],
    ]
    if recruit.attendance_comment:
        details.append(["Attendance comment", recruit.attendance_comment, "", ""])
    row = _write_table(sheet, 4, ["Profile field", "Value", "Profile field", "Value"], details, widths=[18, 25, 20, 25])
    _status_fill(sheet.cell(8, 2), color)
    if recruit.photo_data:
        try:
            source = io.BytesIO(recruit.photo_data)
            output = io.BytesIO()
            with PillowImage.open(source) as image:
                image.convert("RGB").save(output, format="PNG")
            output.seek(0)
            photo = ExcelImage(output)
            photo.width = 145
            photo.height = 145
            sheet.add_image(photo, "F4")
        except Exception:
            sheet.cell(4, 6, "Photo could not be embedded")

    row = max(row, 10)
    row = _section(sheet, row, "Dimension performance", 12)
    dimension_start = row + 1
    dimension_rows = []
    for code in DIMENSION_ORDER:
        item = (result or {}).get("dimensions", {}).get(code, {})
        dimension_rows.append([DIMENSION_NAMES[code], item.get("score", 0), item.get("rank", "—"), "Complete" if item.get("complete") else "Incomplete"])
    row = _write_table(sheet, row, ["Dimension", "Grade /1", "Rank", "Status"], dimension_rows, widths=[25, 14, 10, 16], number_formats={2: "0.00"})
    _add_radar(sheet, dimension_start, dimension_start + len(dimension_rows) - 1, 1, 2, "F11", "Dimension profile /1", 1)

    row = max(row, 23)
    row = _section(sheet, row, "Activity performance", 12)
    activity_start = row + 1
    activity_rows = []
    for code in ACTIVITY_ORDER:
        item = (result or {}).get("activities", {}).get(code, {})
        activity_rows.append([RUBRICS[code].name, item.get("score", 0), item.get("rank", "—"), item.get("submitted", 0), item.get("expected", 0), "Complete" if item.get("complete") else "Incomplete"])
    row = _write_table(sheet, row, ["Activity", "Grade /5", "Rank", "Submitted", "Expected", "Status"], activity_rows, widths=[22, 14, 10, 13, 13, 16], number_formats={2: "0.00"})
    _add_radar(sheet, activity_start, activity_start + len(activity_rows) - 1, 1, 2, "H24", "Activity profile /5", 5)

    row = max(row, 36)
    row = _section(sheet, row, "General assessment, comments and notes", 12)
    general_values = [
        ["Punctuality /1", float(assessment.punctuality) if assessment and assessment.punctuality is not None else "Missing"],
        ["Respect to us /1", float(assessment.respect) if assessment and assessment.respect is not None else "Missing"],
        ["Seriousness /1", float(assessment.seriousness) if assessment and assessment.seriousness is not None else "Missing"],
        ["General average /1", (result or {}).get("generalAverage", 0)],
    ]
    row = _write_table(sheet, row, ["General component", "Grade"], general_values, widths=[24, 16], number_formats={2: "0.00"})
    sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=12)
    sheet.cell(row, 1, f"General admin comment: {(assessment.comment if assessment else '') or 'None'}")
    sheet.cell(row, 1).alignment = Alignment(wrap_text=True, vertical="top")
    sheet.cell(row, 1).fill = PatternFill("solid", fgColor=GRAY_LIGHT)
    sheet.row_dimensions[row].height = 34
    row += 1
    sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=12)
    sheet.cell(row, 1, f"Notes: {(assessment.notes if assessment else '') or 'None'}")
    sheet.cell(row, 1).alignment = Alignment(wrap_text=True, vertical="top")
    sheet.cell(row, 1).fill = PatternFill("solid", fgColor=YELLOW_LIGHT)
    sheet.row_dimensions[row].height = 42
    row += 2

    row = _section(sheet, row, "Evaluator breakdown", 12)
    evaluator_rows = []
    criterion_rows = []
    for assignment in data["assignments"]:
        if assignment.recruit_id != recruit.id:
            continue
        code = data["assignment_activity"].get(assignment.id)
        evaluator = data["evaluator_by_id"].get(assignment.evaluator_id)
        submission = data["submissions"].get(assignment.id)
        evaluator_rows.append([RUBRICS[code].name, evaluator.name if evaluator else "Unknown", evaluator.role.title() if evaluator else "", float(submission.score) if submission else "", _label(submission.status) if submission else "Missing", submission.comments if submission else ""])
        if submission:
            responses = loads(submission.responses_json, {})
            raw = loads(submission.raw_payload_json, {})
            for criterion in RUBRICS[code].criteria:
                result_value = raw.get(criterion.key, "")
                criterion_rows.append([RUBRICS[code].name, criterion.dimension, criterion.name, evaluator.name if evaluator else "Unknown", responses.get(criterion.key, ""), f"{result_value} {criterion.unit}".strip() if result_value != "" else "", _label(submission.status)])
    for evaluation in data["admin_evaluations"]:
        if evaluation.recruit_id != recruit.id:
            continue
        responses = loads(evaluation.responses_json, {})
        raw = loads(evaluation.raw_payload_json, {})
        evaluator_rows.append([RUBRICS[evaluation.activity_code].name, evaluation.updated_by, "Admin", float(evaluation.score), "Official", evaluation.comments])
        for criterion in RUBRICS[evaluation.activity_code].criteria:
            result_value = raw.get(criterion.key, "")
            criterion_rows.append([RUBRICS[evaluation.activity_code].name, criterion.dimension, criterion.name, evaluation.updated_by, responses.get(criterion.key, ""), f"{result_value} {criterion.unit}".strip() if result_value != "" else "", "Official admin evaluation"])
    row = _write_table(sheet, row, ["Activity", "Evaluator", "Role", "Score /5", "Status", "Comment"], evaluator_rows, widths=[18, 26, 13, 13, 15, 42], number_formats={4: "0.00"})
    row = _section(sheet, row, "Criterion-level grading", 12)
    _write_table(sheet, row, ["Activity", "Dimension / theme", "Criterion", "Evaluator", "Grade /5", "Sport result", "Status"], criterion_rows, widths=[18, 19, 38, 25, 13, 16, 14], number_formats={5: "0.00"}, auto_filter=True)


def _audit_sheet(workbook: Workbook, journey: Journey, db: Session) -> None:
    sheet = _new_sheet(workbook, "Audit History", landscape=True)
    _title(sheet, "Administrative History", f"Readable change history · {journey.name}", 8)
    events = list(db.scalars(select(AuditEvent).where(AuditEvent.journey_id == journey.id).order_by(AuditEvent.created_at.desc())))
    rows = [[_local_time(item.created_at), item.actor_name, _label(item.actor_type), _label(item.action), _label(item.entity_type), item.entity_id or "", item.before_json or "", item.after_json or "", item.reason or ""] for item in events]
    _write_table(sheet, 4, ["Date and time (Beirut)", "Username", "Account type", "Action", "Location", "Record ID", "Before", "After", "Reason"], rows, widths=[25, 25, 15, 34, 20, 38, 55, 55, 45], auto_filter=True)


def build_report_workbook(db: Session, journey: Journey, *, full: bool) -> Workbook:
    workbook = Workbook()
    workbook.remove(workbook.active)
    data = _collect(db, journey)
    data["db"] = db
    if full:
        _dashboard_sheet(workbook, journey, data)
        _attendance_sheets(workbook, journey, data)
        _rooms_sheet(workbook, journey, data)
        _assignments_sheet(workbook, journey, data)
        _evaluation_sheet(workbook, journey, data)
        _rubric_sheet(workbook, journey)
    _results_sheets(workbook, journey, data)
    for index, recruit in enumerate(data["recruits"], 1):
        _profile_sheet(workbook, journey, data, recruit, index)
    if full:
        _audit_sheet(workbook, journey, db)
    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True
    return workbook
