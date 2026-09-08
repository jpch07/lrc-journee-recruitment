from __future__ import annotations

import io
import re
from copy import deepcopy
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.chart import RadarChart, Reference
from openpyxl.formatting.rule import FormulaRule
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from PIL import Image as PillowImage, ImageDraw, ImageOps
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
from .excel_cell_images import embed_lookup_images
from .rubric import ACTIVITY_ORDER, DIMENSION_NAMES, DIMENSION_ORDER, RUBRICS
from .scoring import configured_ranks
from .services import latest_room_plan, result_snapshot
from .utils import loads
from .assessment_runtime import active_assessment_definition
from .general_assessment import configured_general_assessment_values
from .object_storage import has_photo, read_recruit_photo


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


def _dimension_maximum(code: str | None = None) -> float:
    dimensions = active_assessment_definition().dimensions
    item = next((entry for entry in dimensions if entry.key == code), None) if code else None
    return float(item.displayMaximum if item else (dimensions[0].displayMaximum if dimensions else 5))


def _dimension_grade(value: object, code: str | None = None) -> float:
    """Convert the internal 0–1 dimension score to its user-facing 0–5 scale."""
    return float(value or 0) * _dimension_maximum(code)


def _official_maximum() -> float:
    return float(active_assessment_definition().scoring.officialMaximum)


def _all_completed_label() -> str:
    """Return the report scope label using the active workspace terminology."""
    return f"All completed {active_assessment_definition().terminology.sessionPlural}"


def management_report_filename() -> str:
    """Return a safe, workspace-specific management-report filename."""
    name = active_assessment_definition().name
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-") or "assessment"
    return f"{safe_name}-management-report.xlsx"


def format_criterion_target(criterion: object) -> str | float:
    """Format duration targets as time while leaving other targets numeric."""
    target = getattr(criterion, "target", None)
    if target is None:
        return ""
    if getattr(criterion, "input_type", getattr(criterion, "inputType", "")) == "duration":
        seconds = max(0, int(Decimal(str(target))))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return float(target)


def _profile_photo_png(photo_data: bytes | None) -> bytes:
    canvas_size = 360
    canvas = PillowImage.new("RGB", (canvas_size, canvas_size), "#F3F5F7")
    if photo_data:
        try:
            with PillowImage.open(io.BytesIO(photo_data)) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.thumbnail((canvas_size, canvas_size), PillowImage.Resampling.LANCZOS)
                canvas.paste(image, ((canvas_size - image.width) // 2, (canvas_size - image.height) // 2))
        except Exception:
            photo_data = None
    if not photo_data:
        draw = ImageDraw.Draw(canvas)
        draw.ellipse((125, 72, 235, 182), fill="#C8102E")
        draw.rounded_rectangle((72, 190, 288, 310), radius=54, fill="#C8102E")
        draw.text((111, 325), "PHOTO NOT RECORDED", fill="#667085")
    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    return output.getvalue()


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
    sheet.sheet_properties.tabColor = {"Attendance": RED, "Results": GREEN, "Recruit Profiles": YELLOW}.get(title, NAVY)
    sheet.sheet_view.showGridLines = False
    sheet.sheet_view.zoomScale = 90
    sheet.freeze_panes = "A4"
    sheet.page_setup.orientation = "landscape" if landscape else "portrait"
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.oddFooter.center.text = f"{active_assessment_definition().name} · View-only export"
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
    fill, color = RED_LIGHT, RED
    if "Dimension" in title or "Criterion" in title:
        fill, color = BLUE_LIGHT, NAVY
    elif "Activity" in title:
        fill, color = GREEN_LIGHT, GREEN
    elif "General" in title:
        fill, color = YELLOW_LIGHT, "745300"
    elif "audit" in title.casefold():
        fill, color = GRAY_LIGHT, NAVY
    cell.fill = PatternFill("solid", fgColor=fill)
    cell.font = Font(name="Aptos", size=11, bold=True, color=color)
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
    averages = [[DIMENSION_NAMES[code], _dimension_grade(data["results"]["dimensionAverages"].get(code, 0), code), f"Dimension /{_dimension_maximum(code):g}"] for code in DIMENSION_ORDER]
    averages.extend([[RUBRICS[code].name, data["results"]["activityAverages"].get(code, 0), "Activity /5"] for code in ACTIVITY_ORDER])
    row = _write_table(sheet, row, ["Measure", "Average", "Scale"], averages, widths=[28, 14, 16], number_formats={2: "0.00"})
    row = _section(sheet, row, "Provisional overall ranking", 12)
    ranking_rows = [[item["overallRank"], item["name"], item["overallScore"], item["color"].title(), item["missingCount"]] for item in data["results"]["rows"]]
    _write_table(sheet, row, ["Rank", "Recruit", f"Overall /{_official_maximum():g}", "Color", "Missing components"], ranking_rows, widths=[10, 30, 16, 13, 20], number_formats={3: "0.00"}, auto_filter=True)


def _attendance_sheets(workbook: Workbook, journey: Journey, data: dict) -> None:
    recruits = _new_sheet(workbook, "Recruit Attendance", landscape=True)
    _title(recruits, "Recruit Attendance", journey.name, 8)
    rows = [[item.name, "Present" if item.present else "Absent", _local_time(item.arrival_time), item.attendance_comment or "", item.phone_number or "", item.date_of_birth, "Yes" if has_photo(item) else "No"] for item in data["recruits"]]
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
        rows.append([RUBRICS[code].name if code else "Unknown", assignment.room_number or "—", recruit.name if recruit else "Unknown", evaluator.name if evaluator else "Unknown", evaluator.role.title() if evaluator else "", _label(submission.status) if submission else "Missing", float(submission.score) if submission else ""])
    _write_table(sheet, 4, ["Activity", "Room", "Recruit", "Evaluator", "Role", "Evaluation status", "Score /5"], rows, widths=[20, 9, 28, 28, 13, 18, 13], number_formats={7: "0.00"}, auto_filter=True)


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
    _title(sheet, "Scoring guide", f"Read-only scoring reference · {journey.name}", 9)
    rows = []
    for code in ACTIVITY_ORDER:
        for criterion in RUBRICS[code].criteria:
            rows.append([RUBRICS[code].name, criterion.dimension, criterion.name, float(criterion.weight), criterion.explanation, _label(criterion.input_type), format_criterion_target(criterion), criterion.unit])
    _write_table(sheet, 4, [active_assessment_definition().terminology.stage, "Dimension / theme", "Criterion", "Weight within dimension", "Explanation", "Input", "Full-score target", "Unit"], rows, widths=[18, 20, 36, 22, 60, 14, 18, 14], number_formats={4: "0%"}, auto_filter=True)


def _results_sheets(workbook: Workbook, journey: Journey, data: dict) -> None:
    summary = _new_sheet(workbook, "Results Summary", landscape=True)
    _title(summary, "Overall Results & Rankings", f"{journey.name} · {data['results']['formula']}", 16)
    headers = ["Rank", "Recruit", f"Overall /{_official_maximum():g}", "Color", "Missing", *[DIMENSION_NAMES[code] + f" /{_dimension_maximum(code):g}" for code in DIMENSION_ORDER], "General", *[RUBRICS[code].name + " /5" for code in ACTIVITY_ORDER]]
    rows = [[item["overallRank"], item["name"], item["overallScore"], item["color"].title(), item["missingCount"], *[_dimension_grade(item["dimensions"][code]["score"], code) for code in DIMENSION_ORDER], item["generalAverage"], *[item["activities"][code]["score"] for code in ACTIVITY_ORDER]] for item in data["results"]["rows"]]
    _write_table(summary, 4, headers, rows, widths=[8, 27, 14, 11, 10, *([15] * len(DIMENSION_ORDER)), 13, *([14] * len(ACTIVITY_ORDER))], number_formats={column: "0.00" for column in range(3, len(headers) + 1) if column not in {4, 5}}, auto_filter=True)
    for row in range(5, 5 + len(rows)):
        _status_fill(summary.cell(row, 4), str(summary.cell(row, 4).value))

    dimensions = _new_sheet(workbook, "Dimension Rankings", landscape=True)
    _title(dimensions, "Dimension Rankings", journey.name, 7)
    row = 4
    for code in DIMENSION_ORDER:
        row = _section(dimensions, row, f"{DIMENSION_NAMES[code]} · Average {_dimension_grade(data['results']['dimensionAverages'].get(code, 0), code):.2f} /{_dimension_maximum(code):g}", 7)
        ranking = sorted(data["results"]["rows"], key=lambda item: (item["dimensions"][code]["rank"] or 10**9, item["name"]))
        rows = [[item["dimensions"][code]["rank"], item["name"], _dimension_grade(item["dimensions"][code]["score"], code), item["dimensions"][code]["availableWeight"], "Complete" if item["dimensions"][code]["complete"] else "Incomplete"] for item in ranking]
        row = _write_table(dimensions, row, ["Rank", "Recruit", f"Grade /{_dimension_maximum(code):g}", "Coverage", "Status"], rows, widths=[9, 30, 14, 13, 15], number_formats={3: "0.00", 4: "0%"})

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
    first_band = sorted(active_assessment_definition().scoring.bands, key=lambda item: item.minimum)[0].key
    color = (result or {}).get("color", first_band)
    _title(sheet, recruit.name, f"Recruit profile · {journey.name}", 12)
    details = [
        ["Attendance", "Present" if recruit.present else "Absent", "Arrival", _local_time(recruit.arrival_time)],
        ["Phone", recruit.phone_number or "Not recorded", "Date of birth", recruit.date_of_birth or "Not recorded"],
        [f"Overall /{_official_maximum():g}", (result or {}).get("overallScore", 0), "Overall rank", (result or {}).get("overallRank", "—")],
        ["Color grade", color.title(), "Missing components", (result or {}).get("missingCount", 8)],
    ]
    if recruit.attendance_comment:
        details.append(["Attendance comment", recruit.attendance_comment, "", ""])
    row = _write_table(sheet, 4, ["Profile field", "Value", "Profile field", "Value"], details, widths=[18, 25, 20, 25])
    _status_fill(sheet.cell(8, 2), color)
    photo_bytes = read_recruit_photo(recruit) if has_photo(recruit) else None
    if photo_bytes:
        try:
            source = io.BytesIO(photo_bytes)
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
        dimension_rows.append([DIMENSION_NAMES[code], _dimension_grade(item.get("score", 0), code), item.get("rank", "—"), "Complete" if item.get("complete") else "Incomplete"])
    row = _write_table(sheet, row, ["Dimension", f"Grade /{_dimension_maximum():g}", "Rank", "Status"], dimension_rows, widths=[25, 14, 10, 16], number_formats={2: "0.00"})
    _add_radar(sheet, dimension_start, dimension_start + len(dimension_rows) - 1, 1, 2, "F11", f"Dimension profile /{_dimension_maximum():g}", _dimension_maximum())

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
    factor_values = configured_general_assessment_values(
        assessment,
        (factor.storageKey for factor in active_assessment_definition().generalFactors),
    )
    general_values = [
        [f"{factor.name} /{float(factor.maximum):g}",
         float(factor_values[factor.storageKey])
         if factor_values[factor.storageKey] is not None else "Missing"]
        for factor in active_assessment_definition().generalFactors
    ]
    general_values.append(["General average", (result or {}).get("generalAverage", 0)])
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
    del journey, full
    return build_management_report_workbook(db)


def _combined_results(journey_data: list[tuple[Journey, dict]]) -> dict:
    rows: list[dict] = []
    for journey, data in journey_data:
        for source in data["results"]["rows"]:
            row = deepcopy(source)
            row["journeyId"] = journey.id
            row["journeyName"] = journey.name
            row["profileKey"] = f"{journey.id}:{row['recruitId']}"
            rows.append(row)
    overall = configured_ranks([(row["profileKey"], Decimal(str(row["overallScore"]))) for row in rows])
    dimensions = {
        code: configured_ranks([(row["profileKey"], Decimal(str(row["dimensions"][code]["score"]))) for row in rows])
        for code in DIMENSION_ORDER
    }
    activities = {
        code: configured_ranks([(row["profileKey"], Decimal(str(row["activities"][code]["score"]))) for row in rows])
        for code in ACTIVITY_ORDER
    }
    for row in rows:
        row["overallRank"] = overall.get(row["profileKey"])
        for code in DIMENSION_ORDER:
            row["dimensions"][code]["rank"] = dimensions[code].get(row["profileKey"])
        for code in ACTIVITY_ORDER:
            row["activities"][code]["rank"] = activities[code].get(row["profileKey"])
    return {"rows": rows}


def _management_data(db: Session) -> tuple[list[Journey], dict[str, dict], dict]:
    journeys = list(db.scalars(
        select(Journey)
        .where(Journey.status == "completed")
        .order_by(Journey.event_date.desc(), Journey.name)
    ))
    by_id: dict[str, dict] = {}
    completed: list[tuple[Journey, dict]] = []
    for journey in journeys:
        data = _collect(db, journey)
        data["db"] = db
        by_id[journey.id] = data
        completed.append((journey, data))
    return journeys, by_id, _combined_results(completed)


def _style_selector(sheet, label_cell: str, value_cell: str, label: str) -> None:
    sheet[label_cell] = label
    sheet[label_cell].font = Font(name="Aptos", size=9, bold=True, color=GRAY)
    sheet[value_cell].fill = PatternFill("solid", fgColor=BLUE_LIGHT)
    sheet[value_cell].font = Font(name="Aptos", size=10, bold=True, color=NAVY)
    sheet[value_cell].border = Border(
        left=Side(style="medium", color=RED), right=THIN_LINE, top=THIN_LINE, bottom=THIN_LINE
    )
    sheet[value_cell].alignment = Alignment(vertical="center")


def _style_formula_rows(sheet, start_row: int, count: int, columns: int) -> None:
    for row in range(start_row, start_row + max(1, count)):
        for column in range(1, columns + 1):
            cell = sheet.cell(row, column)
            cell.font = Font(name="Aptos", size=9, color="20252B")
            cell.alignment = Alignment(vertical="center", wrap_text=column >= 6)
            cell.border = Border(bottom=THIN_LINE)
            if row % 2 == 0:
                cell.fill = PatternFill("solid", fgColor="F7F9FC")
        sheet.row_dimensions[row].height = 23


def _add_text_color_rules(sheet, cell_range: str, first_cell: str) -> None:
    rules = [
        ("Present", GREEN_LIGHT, GREEN), ("Complete", GREEN_LIGHT, GREEN), ("Green", GREEN_LIGHT, GREEN),
        ("Absent", RED_LIGHT, RED), ("Incomplete", YELLOW_LIGHT, "745300"), ("Red", RED_LIGHT, RED),
        ("Yellow", YELLOW_LIGHT, "745300"),
    ]
    for band in active_assessment_definition().scoring.bands:
        rules.append((band.name, band.color.lstrip("#").upper(), WHITE))
    column = re.sub(r"\d", "", first_cell)
    row = re.sub(r"\D", "", first_cell)
    for value, fill, font in rules:
        sheet.conditional_formatting.add(
            cell_range,
            FormulaRule(
                formula=[f'${column}{row}="{value}"'],
                fill=PatternFill("solid", fgColor=fill),
                font=Font(name="Aptos", size=9, bold=True, color=font),
            ),
        )


def _validation(sheet, cell: str, source_column: str, count: int) -> None:
    if count <= 0:
        return
    validation = DataValidation(type="list", formula1=f"=${source_column}$2:${source_column}${count + 1}", allow_blank=False)
    validation.error = "Choose a value from the list."
    validation.errorTitle = "Invalid selection"
    validation.prompt = "Select from the dropdown."
    validation.promptTitle = "Report selector"
    validation.showErrorMessage = True
    validation.showInputMessage = True
    sheet.add_data_validation(validation)
    validation.add(sheet[cell])


def _write_hidden_rows(sheet, start_column: int, headers: list[str], rows: list[list]) -> tuple[int, int]:
    for offset, header in enumerate(headers):
        sheet.cell(1, start_column + offset, header)
        sheet.column_dimensions[get_column_letter(start_column + offset)].hidden = True
    for row_index, values in enumerate(rows, 2):
        for offset, value in enumerate(values):
            sheet.cell(row_index, start_column + offset, value)
    return 2, max(2, len(rows) + 1)


def _with_lookup_keys(rows: list[list], *key_indexes: int) -> list[list]:
    counters: defaultdict[tuple[str, ...], int] = defaultdict(int)
    keyed: list[list] = []
    for row in rows:
        group = tuple(str(row[index]) for index in key_indexes)
        counters[group] += 1
        keyed.append([*row, "|".join((*group, str(counters[group])))])
    return keyed


def _lookup_value_formula(
    *,
    key_expression: str,
    lookup_column: str,
    result_column: str,
    start: int,
    end: int,
) -> str:
    lookup = f'_xlfn.XLOOKUP({key_expression},${lookup_column}${start}:${lookup_column}${end},${result_column}${start}:${result_column}${end})'
    return f'=IFERROR(IF({lookup}="","",{lookup}),"")'


def _scalar_lookup_formula(key_expression: str, lookup_range: str, result_range: str, *, blank: str = "—") -> str:
    lookup = f"_xlfn.XLOOKUP({key_expression},{lookup_range},{result_range})"
    escaped_blank = blank.replace('"', '""')
    return f'=IFERROR(IF({lookup}="","{escaped_blank}",{lookup}),"{escaped_blank}")'


def _management_attendance_sheet(workbook: Workbook, db: Session, journeys: list[Journey], by_id: dict[str, dict]) -> None:
    terms = active_assessment_definition().terminology
    all_completed = _all_completed_label()
    sheet = _new_sheet(workbook, "Attendance", landscape=True)
    sheet.freeze_panes = "A6"
    _title(sheet, f"{terms.participant} attendance", f"Confirmed {terms.participant.lower()} attendance for completed {terms.sessionPlural}", 7)
    _style_selector(sheet, "A3", "B3", f"{terms.session} view")
    scopes = [all_completed, *[journey.name for journey in journeys]]
    sheet["B3"] = all_completed
    rows: list[list] = []
    for journey in journeys:
        data = by_id[journey.id]
        record_scopes = [journey.name, all_completed]
        for scope in record_scopes:
            for recruit in data["recruits"]:
                rows.append([scope, journey.name, recruit.name, recruit.phone_number or "", recruit.date_of_birth, "Present" if recruit.present else "Absent", _local_time(recruit.arrival_time) if recruit.arrival_time else "", recruit.attendance_comment or ""])
    rows = _with_lookup_keys(rows, 0)
    start, end = _write_hidden_rows(sheet, 13, ["Scope", terms.session, terms.participant, "Phone number", "Date of birth", "Status", "Arrival time", "Attendance comment", "Lookup key"], rows)
    scope_col = 23
    for index, value in enumerate(scopes, 2):
        sheet.cell(index, scope_col, value)
    sheet.cell(1, scope_col, "Scope options")
    sheet.column_dimensions[get_column_letter(scope_col)].hidden = True
    _validation(sheet, "B3", "W", len(scopes))
    headers = [terms.session, terms.participant, "Phone number", "Date of birth", "Status", "Arrival time", "Attendance comment"]
    for column, header in enumerate(headers, 1):
        cell = sheet.cell(5, column, header); cell.fill = PatternFill("solid", fgColor=NAVY); cell.font = Font(name="Aptos", size=9, bold=True, color=WHITE)
    maximum_rows = max(Counter(row[0] for row in rows).values(), default=1)
    for visible_row in range(6, 6 + maximum_rows):
        for column, source_column in enumerate(("N", "O", "P", "Q", "R", "S", "T"), 1):
            sheet.cell(visible_row, column, _lookup_value_formula(
                key_expression=f'$B$3&"|"&ROWS($A$6:$A{visible_row})',
                lookup_column="U", result_column=source_column, start=start, end=end,
            ))
    _style_formula_rows(sheet, 6, maximum_rows, len(headers))
    _add_text_color_rules(sheet, f"E6:E{5 + maximum_rows}", "E6")
    for column, width in enumerate([29, 28, 19, 17, 13, 24, 38], 1): sheet.column_dimensions[get_column_letter(column)].width = width
    for visible_row in range(6, 6 + maximum_rows):
        sheet.cell(visible_row, 4).number_format = "dd mmm yyyy"
    sheet.auto_filter.ref = "A5:G5"


def _result_rows(scope: str, journey_name: str, results: dict) -> list[list]:
    rows: list[list] = []
    for item in results["rows"]:
        display_journey = item.get("journeyName", journey_name)
        rows.append([scope, "Overall ranking", item["overallRank"], item["name"], display_journey, item["overallScore"], f"/{_official_maximum():g}", f"{item['missingCount']} missing", "Complete" if item["complete"] else "Incomplete", item["color"].title(), item.get("generalComment", ""), item.get("notes", "")])
        for code in DIMENSION_ORDER:
            value = item["dimensions"][code]
            rows.append([scope, DIMENSION_NAMES[code], value["rank"], item["name"], display_journey, _dimension_grade(value["score"], code), f"/{_dimension_maximum(code):g}", f"{round(value.get('availableWeight', 0) * 100)}% coverage", "Complete" if value["complete"] else "Incomplete", "", "", ""])
        for code in ACTIVITY_ORDER:
            value = item["activities"][code]
            rows.append([scope, RUBRICS[code].name, value["rank"], item["name"], display_journey, value["score"], "/5", f"{value['submitted']}/{value['expected']} submitted", "Complete" if value["complete"] else "Incomplete", "", "", ""])
    return rows


def _management_results_sheet(workbook: Workbook, journeys: list[Journey], by_id: dict[str, dict], combined: dict) -> None:
    terms = active_assessment_definition().terminology
    all_completed = _all_completed_label()
    sheet = _new_sheet(workbook, "Results", landscape=True)
    sheet.freeze_panes = "A6"
    _title(sheet, "Results & rankings", "Overall, dimension, and activity rankings", 10)
    _style_selector(sheet, "A3", "B3", f"{terms.session} view")
    _style_selector(sheet, "D3", "E3", "Result view")
    scopes = [all_completed, *[journey.name for journey in journeys]]
    views = ["Overall ranking", *[DIMENSION_NAMES[code] for code in DIMENSION_ORDER], *[RUBRICS[code].name for code in ACTIVITY_ORDER]]
    sheet["B3"] = all_completed; sheet["E3"] = "Overall ranking"
    rows = _result_rows(all_completed, "", combined)
    for journey in journeys:
        rows.extend(_result_rows(journey.name, journey.name, by_id[journey.id]["results"]))
    rows = _with_lookup_keys(rows, 0, 1)
    start, end = _write_hidden_rows(sheet, 13, ["Scope", "View", "Rank", terms.participant, terms.session, "Score", "Scale", "Details", "Status", "Color", "General comment", "Notes", "Lookup key"], rows)
    for index, value in enumerate(scopes, 2): sheet.cell(index, 26, value)
    for index, value in enumerate(views, 2): sheet.cell(index, 27, value)
    sheet.cell(1, 26, "Scope options"); sheet.cell(1, 27, "View options")
    sheet.column_dimensions["Z"].hidden = True; sheet.column_dimensions["AA"].hidden = True
    _validation(sheet, "B3", "Z", len(scopes)); _validation(sheet, "E3", "AA", len(views))
    headers = ["Rank", terms.participant, terms.session, "Score", "Scale", "Details", "Status", "Color", "General comment", "Notes"]
    for column, header in enumerate(headers, 1):
        cell = sheet.cell(5, column, header); cell.fill = PatternFill("solid", fgColor=NAVY); cell.font = Font(name="Aptos", size=9, bold=True, color=WHITE)
    maximum_rows = max(Counter((row[0], row[1]) for row in rows).values(), default=1)
    for visible_row in range(6, 6 + maximum_rows):
        for column, source_column in enumerate(("O", "P", "Q", "R", "S", "T", "U", "V", "W", "X"), 1):
            sheet.cell(visible_row, column, _lookup_value_formula(
                key_expression=f'$B$3&"|"&$E$3&"|"&ROWS($A$6:$A{visible_row})',
                lookup_column="Y", result_column=source_column, start=start, end=end,
            ))
    _style_formula_rows(sheet, 6, maximum_rows, len(headers))
    _add_text_color_rules(sheet, f"G6:G{5 + maximum_rows}", "G6")
    _add_text_color_rules(sheet, f"H6:H{5 + maximum_rows}", "H6")
    for column, width in enumerate([10, 30, 27, 14, 10, 23, 15, 12, 40, 40], 1): sheet.column_dimensions[get_column_letter(column)].width = width
    for visible_row in range(6, 6 + maximum_rows):
        sheet.cell(visible_row, 4).number_format = "0.00"
        sheet.row_dimensions[visible_row].height = 44


def _fallback_result(recruit: Recruit) -> dict:
    definition = active_assessment_definition()
    factors = [item.name for item in definition.generalFactors]
    first_band = sorted(definition.scoring.bands, key=lambda item: item.minimum)[0].key
    return {
        "overallScore": 0.0, "overallRank": None, "color": first_band, "missingCount": len(ACTIVITY_ORDER) + len(factors),
        "missingComponents": [RUBRICS[code].name for code in ACTIVITY_ORDER] + factors,
        "dimensions": {code: {"score": 0.0, "rank": None, "complete": False, "availableWeight": 0.0} for code in DIMENSION_ORDER},
        "activities": {code: {"score": 0.0, "rank": None, "complete": False, "submitted": 0, "expected": 0} for code in ACTIVITY_ORDER},
        "generalAverage": 0.0, "complete": False, "name": recruit.name,
    }


def _profile_label(recruit: Recruit, totals: Counter[str], seen: defaultdict[str, int]) -> str:
    name = recruit.name.strip()
    key = name.casefold()
    seen[key] += 1
    return name if totals[key] == 1 else f"{name} ({seen[key]})"


def _management_profile_sheet(workbook: Workbook, db: Session, journeys: list[Journey], by_id: dict[str, dict], combined: dict) -> None:
    terms = active_assessment_definition().terminology
    all_completed = _all_completed_label()
    sheet = _new_sheet(workbook, "Recruit Profiles", landscape=True)
    general_factors = active_assessment_definition().generalFactors
    summary_headers = [
        "Selection", "Profile key", terms.session, "Date", terms.participant, "Phone", "DOB",
        "Attendance", "Arrival", "Attendance comment", "Overall", "Rank", "Color", "Missing",
        *[factor.name for factor in general_factors],
        "General average", "General comment", "Notes",
    ]
    dimension_headers = ["Selection", "Dimension", "Score", "Rank", "Status", "Coverage"]
    activity_headers = ["Selection", "Activity", "Score", "Rank", "Submissions", "Status"]
    evaluator_data_headers = ["Profile key", terms.stage, terms.assessor, "Category", "Score", "Status", "Comment", "Lookup key"]
    criterion_data_headers = ["Profile key", terms.stage, "Dimension", "Criterion", "Explanation", terms.assessor, "Grade", "Raw result", "Status", "Lookup key"]
    audit_data_headers = ["Profile key", "Date", "Username", "Action", "Reason", "Before", "After", "Lookup key"]
    summary_column = 27
    dimension_column = max(49, summary_column + len(summary_headers) + 2)
    activity_column = dimension_column + len(dimension_headers) + 1
    evaluator_column = activity_column + len(activity_headers) + 1
    criterion_column = evaluator_column + len(evaluator_data_headers)
    audit_column = criterion_column + len(criterion_data_headers)
    scope_options_column = audit_column + len(audit_data_headers)
    recruit_options_column = scope_options_column + 1
    photo_key_column = recruit_options_column + 1
    photo_value_column = photo_key_column + 1

    def hidden_column(block_start: int, offset: int) -> str:
        return get_column_letter(block_start + offset)

    sheet.freeze_panes = "A8"
    _title(sheet, f"{terms.participant} profile", f"Complete view-only {terms.participant.lower()} record", 12)
    _style_selector(sheet, "A3", "B3", f"{terms.session} view")
    _style_selector(sheet, "D3", "E3", terms.participant)
    name_totals: Counter[str] = Counter(
        recruit.name.strip().casefold()
        for journey in journeys
        for recruit in by_id[journey.id]["recruits"]
    )
    name_seen: defaultdict[str, int] = defaultdict(int)
    combined_by_key = {row["profileKey"]: row for row in combined["rows"]}
    summary_rows: list[list] = []; dimension_rows: list[list] = []; activity_rows: list[list] = []
    evaluator_rows: list[list] = []; criterion_rows: list[list] = []; audit_rows: list[list] = []
    selector_labels: list[str] = []
    profile_photos: list[tuple[str, bytes]] = []
    scope_names = [all_completed, *[journey.name for journey in journeys]]
    for journey in journeys:
        data = by_id[journey.id]
        for recruit in data["recruits"]:
            profile_key = f"{journey.id}:{recruit.id}"
            profile_photos.append((profile_key, _profile_photo_png(read_recruit_photo(recruit) if has_photo(recruit) else None)))
            label = _profile_label(recruit, name_totals, name_seen)
            selector_labels.append(label)
            base_result = data["result_by_recruit"].get(recruit.id) or _fallback_result(recruit)
            scoped = [(journey.name, base_result)]
            scoped.append((all_completed, combined_by_key.get(profile_key, base_result)))
            assessment = data["assessments"].get(recruit.id)
            factor_values = configured_general_assessment_values(
                assessment, (factor.storageKey for factor in general_factors)
            )
            for scope, result in scoped:
                selection_key = f"{scope}|{label}"
                summary_rows.append([
                    selection_key, profile_key, journey.name, journey.event_date, recruit.name,
                    recruit.phone_number or "", recruit.date_of_birth,
                    "Present" if recruit.present else "Absent",
                    _local_time(recruit.arrival_time) if recruit.arrival_time else "",
                    recruit.attendance_comment or "", result["overallScore"], result["overallRank"] or "",
                    result["color"].title(), ", ".join(result.get("missingComponents", [])) or "Complete",
                    *[
                        float(factor_values[factor.storageKey])
                        if factor_values[factor.storageKey] is not None else ""
                        for factor in general_factors
                    ],
                    float(result.get("generalAverage", 0)), assessment.comment if assessment else "",
                    assessment.notes if assessment else "",
                ])
                for code in DIMENSION_ORDER:
                    value = result["dimensions"][code]
                    dimension_rows.append([selection_key, DIMENSION_NAMES[code], _dimension_grade(value["score"], code), value["rank"] or "", "Complete" if value["complete"] else "Incomplete", f"{round(value.get('availableWeight', 0) * 100)}%"])
                for code in ACTIVITY_ORDER:
                    value = result["activities"][code]
                    activity_rows.append([selection_key, RUBRICS[code].name, value["score"], value["rank"] or "", f"{value['submitted']}/{value['expected']}", "Complete" if value["complete"] else "Incomplete"])
            for assignment in data["assignments"]:
                if assignment.recruit_id != recruit.id: continue
                code = data["assignment_activity"].get(assignment.id)
                evaluator = data["evaluator_by_id"].get(assignment.evaluator_id)
                submission = data["submissions"].get(assignment.id)
                evaluator_rows.append([profile_key, RUBRICS[code].name if code else "", evaluator.name if evaluator else "Unknown", evaluator.role.title() if evaluator else "", float(submission.score) if submission else "", _label(submission.status) if submission else "Missing", submission.comments if submission else ""])
                if code and submission:
                    responses = loads(submission.responses_json, {}); raw = loads(submission.raw_payload_json, {})
                    for criterion in RUBRICS[code].criteria:
                        raw_value = raw.get(criterion.key, "")
                        criterion_rows.append([profile_key, RUBRICS[code].name, criterion.dimension, criterion.name, criterion.explanation, evaluator.name if evaluator else "Unknown", responses.get(criterion.key, ""), f"{raw_value} {criterion.unit}".strip() if raw_value != "" else "", _label(submission.status)])
            for evaluation in data["admin_evaluations"]:
                if evaluation.recruit_id != recruit.id: continue
                evaluator_rows.append([profile_key, RUBRICS[evaluation.activity_code].name, f"Admin: {evaluation.updated_by}", "Admin", float(evaluation.score), "Official", evaluation.comments])
                responses = loads(evaluation.responses_json, {}); raw = loads(evaluation.raw_payload_json, {})
                for criterion in RUBRICS[evaluation.activity_code].criteria:
                    raw_value = raw.get(criterion.key, "")
                    criterion_rows.append([profile_key, RUBRICS[evaluation.activity_code].name, criterion.dimension, criterion.name, criterion.explanation, f"Admin: {evaluation.updated_by}", responses.get(criterion.key, ""), f"{raw_value} {criterion.unit}".strip() if raw_value != "" else "", "Official admin evaluation"])
            for event in db.scalars(select(AuditEvent).where(AuditEvent.journey_id == journey.id, AuditEvent.entity_id == recruit.id).order_by(AuditEvent.created_at.desc())):
                audit_rows.append([profile_key, _local_time(event.created_at), event.actor_name, _label(event.action), event.reason or "", event.before_json or "", event.after_json or ""])
    selector_labels = sorted(set(selector_labels), key=str.casefold)
    default_label = next((label for label in selector_labels if any(row[0] == f"{all_completed}|{label}" for row in summary_rows)), selector_labels[0] if selector_labels else "")
    sheet["B3"] = all_completed if any(j.status == "completed" for j in journeys) else (journeys[0].name if journeys else "")
    sheet["E3"] = default_label
    _write_hidden_rows(sheet, summary_column, summary_headers, summary_rows)
    _write_hidden_rows(sheet, dimension_column, dimension_headers, dimension_rows)
    _write_hidden_rows(sheet, activity_column, activity_headers, activity_rows)
    evaluator_rows = _with_lookup_keys(evaluator_rows, 0)
    criterion_rows = _with_lookup_keys(criterion_rows, 0)
    audit_rows = _with_lookup_keys(audit_rows, 0)
    _write_hidden_rows(sheet, evaluator_column, evaluator_data_headers, evaluator_rows)
    _write_hidden_rows(sheet, criterion_column, criterion_data_headers, criterion_rows)
    _write_hidden_rows(sheet, audit_column, audit_data_headers, audit_rows)
    for index, value in enumerate(scope_names, 2): sheet.cell(index, scope_options_column, value)
    for index, value in enumerate(selector_labels, 2): sheet.cell(index, recruit_options_column, value)
    scope_options_letter = get_column_letter(scope_options_column)
    recruit_options_letter = get_column_letter(recruit_options_column)
    sheet.column_dimensions[scope_options_letter].hidden = True
    sheet.column_dimensions[recruit_options_letter].hidden = True
    _validation(sheet, "B3", scope_options_letter, len(scope_names))
    _validation(sheet, "E3", recruit_options_letter, len(selector_labels))
    last_summary = max(2, len(summary_rows) + 1); last_dimension = max(2, len(dimension_rows) + 1); last_activity = max(2, len(activity_rows) + 1)
    selection = '$B$3&"|"&$E$3'
    summary_selection_letter = hidden_column(summary_column, 0)
    sheet["H3"] = _scalar_lookup_formula(
        selection,
        f"${summary_selection_letter}$2:${summary_selection_letter}${last_summary}",
        f"${hidden_column(summary_column, 1)}$2:${hidden_column(summary_column, 1)}${last_summary}",
        blank="",
    )
    sheet["H3"].number_format = ";;;"
    fields = [
        ("A5", terms.participant, hidden_column(summary_column, 4)),
        ("D5", terms.session, hidden_column(summary_column, 2)),
        ("G5", "Date", hidden_column(summary_column, 3)),
        ("A6", "Phone", hidden_column(summary_column, 5)),
        ("D6", "Date of birth", hidden_column(summary_column, 6)),
        ("G6", "Arrival", hidden_column(summary_column, 8)),
        ("A7", "Attendance", hidden_column(summary_column, 7)),
    ]
    for cell, label, source in fields:
        sheet[cell] = f'{label}: '; sheet[cell].font = Font(name="Aptos", size=9, bold=True, color=GRAY)
        value_cell = sheet.cell(sheet[cell].row, sheet[cell].column + 1)
        value_cell.value = _scalar_lookup_formula(
            selection,
            f"${summary_selection_letter}$2:${summary_selection_letter}${last_summary}",
            f"${source}$2:${source}${last_summary}",
        )
        value_cell.font = Font(name="Aptos", size=11, bold=True, color=NAVY)
    sheet["E6"].number_format = "dd mmm yyyy"
    sheet["H5"].number_format = "dd mmm yyyy"
    sheet["D7"] = "Overall:"
    sheet["D7"].font = Font(name="Aptos", size=9, bold=True, color=GRAY)
    overall_letter = hidden_column(summary_column, 10)
    rank_letter = hidden_column(summary_column, 11)
    sheet["E7"] = f'=IFERROR(TEXT(_xlfn.XLOOKUP({selection},${summary_selection_letter}$2:${summary_selection_letter}${last_summary},${overall_letter}$2:${overall_letter}${last_summary}),"0.00")&" /{_official_maximum():g} · rank "&_xlfn.XLOOKUP({selection},${summary_selection_letter}$2:${summary_selection_letter}${last_summary},${rank_letter}$2:${rank_letter}${last_summary}),"—")'
    sheet["E7"].font = Font(name="Aptos", size=11, bold=True, color=NAVY)
    sheet["G7"] = "Color grade:"
    sheet["G7"].font = Font(name="Aptos", size=9, bold=True, color=GRAY)
    sheet.merge_cells("H7:I7")
    color_letter = hidden_column(summary_column, 12)
    sheet["H7"] = _scalar_lookup_formula(
        selection,
        f"${summary_selection_letter}$2:${summary_selection_letter}${last_summary}",
        f"${color_letter}$2:${color_letter}${last_summary}",
    )
    sheet["H7"].font = Font(name="Aptos", size=10, bold=True, color=WHITE)
    sheet["H7"].alignment = Alignment(horizontal="center", vertical="center")
    sheet.row_dimensions[7].height = 27
    _add_text_color_rules(sheet, "H7:I7", "H7")
    if profile_photos:
        photo_start = 2
        for photo_row, (profile_key, _) in enumerate(profile_photos, photo_start):
            sheet.cell(photo_row, photo_key_column, profile_key)
            photo_cell = sheet.cell(photo_row, photo_value_column, "#VALUE!")
            photo_cell.data_type = "e"
        photo_key_letter = get_column_letter(photo_key_column)
        photo_value_letter = get_column_letter(photo_value_column)
        sheet.column_dimensions[photo_key_letter].hidden = True
        sheet.column_dimensions[photo_value_letter].hidden = True
        photo_end = photo_start + len(profile_photos) - 1
        sheet.merge_cells("J3:L7")
        sheet["J3"] = f'=_xlfn.XLOOKUP($H$3,${photo_key_letter}$2:${photo_key_letter}${photo_end},${photo_value_letter}$2:${photo_value_letter}${photo_end})'
        sheet["J3"].alignment = Alignment(horizontal="center", vertical="center")
        sheet["J3"].fill = PatternFill("solid", fgColor=GRAY_LIGHT)
        default_profile_key = next((item[1] for item in summary_rows if item[0] == f"{sheet['B3'].value}|{default_label}"), profile_photos[0][0])
        initial_photo_index = next((index for index, item in enumerate(profile_photos) if item[0] == default_profile_key), 0)
        workbook._journee_profile_images = {
            "sheet_name": sheet.title,
            "image_cells": [(f"{photo_value_letter}{row}", photo_bytes) for row, (_, photo_bytes) in enumerate(profile_photos, photo_start)],
            "formula_cell": "J3",
            "initial_image_index": initial_photo_index,
        }
    row = _section(sheet, 8, "Dimension performance", 12)
    for col, header in enumerate(["Dimension", f"Score /{_dimension_maximum():g}", "Rank", "Status", "Coverage"], 1):
        sheet.cell(row, col, header).fill = PatternFill("solid", fgColor=NAVY); sheet.cell(row, col).font = Font(name="Aptos", size=9, bold=True, color=WHITE)
    for offset, code in enumerate(DIMENSION_ORDER, 1):
        target = row + offset; sheet.cell(target, 1, DIMENSION_NAMES[code])
        for col, source_offset in enumerate(range(2, 6), 2):
            source_col = hidden_column(dimension_column, source_offset)
            dimension_selection_col = hidden_column(dimension_column, 0)
            dimension_name_col = hidden_column(dimension_column, 1)
            sheet.cell(target, col, _scalar_lookup_formula(
                f'{selection}&"|"&$A{target}',
                f'${dimension_selection_col}$2:${dimension_selection_col}${last_dimension}&"|"&${dimension_name_col}$2:${dimension_name_col}${last_dimension}',
                f'${source_col}$2:${source_col}${last_dimension}',
            ))
    _style_formula_rows(sheet, row + 1, len(DIMENSION_ORDER), 5)
    _add_text_color_rules(sheet, f"D{row + 1}:D{row + len(DIMENSION_ORDER)}", f"D{row + 1}")
    _add_radar(sheet, row + 1, row + len(DIMENSION_ORDER), 1, 2, "G8", f"Dimension performance /{_dimension_maximum():g}", _dimension_maximum())
    activity_section = 26
    row = _section(sheet, activity_section, "Activity performance", 12)
    for col, header in enumerate([terms.stage, "Score /5", "Rank", "Submissions", "Status"], 1):
        sheet.cell(row, col, header).fill = PatternFill("solid", fgColor=NAVY); sheet.cell(row, col).font = Font(name="Aptos", size=9, bold=True, color=WHITE)
    for offset, code in enumerate(ACTIVITY_ORDER, 1):
        target = row + offset; sheet.cell(target, 1, RUBRICS[code].name)
        for col, source_offset in enumerate(range(2, 6), 2):
            source_col = hidden_column(activity_column, source_offset)
            activity_selection_col = hidden_column(activity_column, 0)
            activity_name_col = hidden_column(activity_column, 1)
            sheet.cell(target, col, _scalar_lookup_formula(
                f'{selection}&"|"&$A{target}',
                f'${activity_selection_col}$2:${activity_selection_col}${last_activity}&"|"&${activity_name_col}$2:${activity_name_col}${last_activity}',
                f'${source_col}$2:${source_col}${last_activity}',
            ))
    _style_formula_rows(sheet, row + 1, len(ACTIVITY_ORDER), 5)
    _add_text_color_rules(sheet, f"E{row + 1}:E{row + len(ACTIVITY_ORDER)}", f"E{row + 1}")
    _add_radar(sheet, row + 1, row + len(ACTIVITY_ORDER), 1, 2, "G26", "Activity performance", 5)
    row = _section(sheet, 44, "General assessment and completion", 12)
    labels = [
        *[
            (f"{factor.name} /{float(factor.maximum):g}", hidden_column(summary_column, 14 + index))
            for index, factor in enumerate(general_factors)
        ],
        ("General average /1", hidden_column(summary_column, 14 + len(general_factors))),
        ("Missing components", hidden_column(summary_column, 13)),
        ("General comment", hidden_column(summary_column, 15 + len(general_factors))),
        ("Notes", hidden_column(summary_column, 16 + len(general_factors))),
    ]
    for index, (label, source) in enumerate(labels):
        target = row + index; sheet.cell(target, 1, label); sheet.cell(target, 1).font = Font(name="Aptos", size=9, bold=True, color=GRAY)
        sheet.merge_cells(start_row=target, start_column=2, end_row=target, end_column=12)
        sheet.cell(target, 2, _scalar_lookup_formula(
            selection,
            f"${summary_selection_letter}$2:${summary_selection_letter}${last_summary}",
            f"${source}$2:${source}${last_summary}",
        ))
        sheet.cell(target, 2).alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
        sheet.cell(target, 2).fill = PatternFill("solid", fgColor="FBFCFE")
        sheet.cell(target, 2).border = Border(bottom=THIN_LINE)
        sheet.row_dimensions[target].height = 38 if label in {"General comment", "Notes"} else 24
    evaluator_maximum = max(Counter(item[0] for item in evaluator_rows).values(), default=1)
    evaluator_section = max(55, row + len(labels) + 2)
    row = _section(sheet, evaluator_section, f"{terms.assessor} breakdown", 12)
    evaluator_headers = [terms.stage, terms.assessor, "Category", "Score /5", "Status", "Comment"]
    for col, header in enumerate(evaluator_headers, 1): sheet.cell(row, col, header).fill = PatternFill("solid", fgColor=NAVY); sheet.cell(row, col).font = Font(name="Aptos", size=9, bold=True, color=WHITE)
    evaluator_end = max(2, len(evaluator_rows) + 1)
    evaluator_visible_start = row + 1
    for visible_row in range(evaluator_visible_start, evaluator_visible_start + evaluator_maximum):
        for column, source_offset in enumerate(range(1, 7), 1):
            source_column = hidden_column(evaluator_column, source_offset)
            sheet.cell(visible_row, column, _lookup_value_formula(
                key_expression=f'$H$3&"|"&ROWS($A${evaluator_visible_start}:$A{visible_row})',
                lookup_column=hidden_column(evaluator_column, 7),
                result_column=source_column,
                start=2,
                end=evaluator_end,
            ))
    _style_formula_rows(sheet, evaluator_visible_start, evaluator_maximum, len(evaluator_headers))
    _add_text_color_rules(sheet, f"E{evaluator_visible_start}:E{evaluator_visible_start + evaluator_maximum - 1}", f"E{evaluator_visible_start}")
    criterion_section = max(79, evaluator_visible_start + evaluator_maximum + 2)
    row = _section(sheet, criterion_section, "Criterion-level grading", 12)
    criterion_headers = [terms.stage, "Dimension", "Criterion", "Explanation", terms.assessor, "Grade /5", "Raw result", "Status"]
    for col, header in enumerate(criterion_headers, 1): sheet.cell(row, col, header).fill = PatternFill("solid", fgColor=NAVY); sheet.cell(row, col).font = Font(name="Aptos", size=9, bold=True, color=WHITE)
    criterion_end = max(2, len(criterion_rows) + 1)
    criterion_visible_start = row + 1
    criterion_maximum = min(116, max(Counter(item[0] for item in criterion_rows).values(), default=1))
    for visible_row in range(criterion_visible_start, criterion_visible_start + criterion_maximum):
        for column, source_offset in enumerate(range(1, 9), 1):
            source_column = hidden_column(criterion_column, source_offset)
            sheet.cell(visible_row, column, _lookup_value_formula(
                key_expression=f'$H$3&"|"&ROWS($A${criterion_visible_start}:$A{visible_row})',
                lookup_column=hidden_column(criterion_column, 9),
                result_column=source_column,
                start=2,
                end=criterion_end,
            ))
    _style_formula_rows(sheet, criterion_visible_start, criterion_maximum, len(criterion_headers))
    _add_text_color_rules(sheet, f"H{criterion_visible_start}:H{criterion_visible_start + criterion_maximum - 1}", f"H{criterion_visible_start}")
    audit_section = max(200, criterion_visible_start + criterion_maximum + 2)
    row = _section(sheet, audit_section, "Profile audit history", 12)
    audit_headers = ["Date and time", "Username", "Action", "Reason", "Before", "After"]
    for col, header in enumerate(audit_headers, 1): sheet.cell(row, col, header).fill = PatternFill("solid", fgColor=NAVY); sheet.cell(row, col).font = Font(name="Aptos", size=9, bold=True, color=WHITE)
    audit_end = max(2, len(audit_rows) + 1)
    audit_visible_start = row + 1
    audit_maximum = max(Counter(item[0] for item in audit_rows).values(), default=1)
    for visible_row in range(audit_visible_start, audit_visible_start + audit_maximum):
        for column, source_offset in enumerate(range(1, 7), 1):
            source_column = hidden_column(audit_column, source_offset)
            sheet.cell(visible_row, column, _lookup_value_formula(
                key_expression=f'$H$3&"|"&ROWS($A${audit_visible_start}:$A{visible_row})',
                lookup_column=hidden_column(audit_column, 7),
                result_column=source_column,
                start=2,
                end=audit_end,
            ))
    _style_formula_rows(sheet, audit_visible_start, audit_maximum, len(audit_headers))
    for col, width in enumerate([24, 17, 12, 34, 24, 16, 19, 16, 16, 16, 16, 16], 1): sheet.column_dimensions[get_column_letter(col)].width = width
    sheet.row_dimensions[5].height = 24; sheet.row_dimensions[6].height = 30


def save_management_report(workbook: Workbook, output: io.BytesIO) -> None:
    base = io.BytesIO()
    workbook.save(base)
    image_config = getattr(workbook, "_journee_profile_images", None)
    content = base.getvalue()
    if image_config:
        content = embed_lookup_images(content, **image_config)
    output.write(content)


def build_management_report_workbook(db: Session) -> Workbook:
    workbook = Workbook()
    workbook.remove(workbook.active)
    journeys, by_id, combined = _management_data(db)
    _management_attendance_sheet(workbook, db, journeys, by_id)
    _management_results_sheet(workbook, journeys, by_id, combined)
    _management_profile_sheet(workbook, db, journeys, by_id, combined)
    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True
    workbook.calculation.calcMode = "auto"
    workbook.calculation.calcId = 0
    return workbook
