"""Literal Google Sheet presentation grids and bounded layout descriptors."""
from __future__ import annotations

import base64
from collections import Counter
from hashlib import sha256
from io import BytesIO
import json
import re
from urllib.parse import parse_qsl, unquote, urlsplit

from PIL import Image, ImageDraw


CHUNK_SIZE = 12_000
MAX_PREVIEW_BYTES = 512 * 1024
MAX_PRESENTATION_COLUMNS = 128
SECRET = re.compile(r"password|secret|token|credential|authorization|apikey|accesskey", re.I)
URL = re.compile(r"(?:[a-z][a-z0-9+.-]*://|/)[^\s\"<>]+", re.I)


class PresentationError(ValueError):
    pass


def _redact_url(match: re.Match[str]) -> str:
    original = match.group(0)
    try:
        parsed = urlsplit(unquote(original))
        sensitive = (
            parsed.scheme.lower() in {"postgres", "postgresql", "cockroachdb"}
            or parsed.username is not None
            or parsed.password is not None
            or re.search(r"/(?:j|e|evaluate|attendance|recruit-attendance)/[^/]+", parsed.path, re.I)
            or any(
                SECRET.search(re.sub(r"[^a-z]", "", key.lower()))
                or key.lower() in {"key", "signature"}
                or key.lower().startswith(("x-amz-", "x-goog-"))
                for key, _ in parse_qsl(parsed.query)
            )
        )
    except ValueError:
        sensitive = True
    return "[credential-bearing URL excluded]" if sensitive else original


def _safe_value(value):
    if isinstance(value, dict):
        return {
            str(key): _safe_value(item)
            for key, item in value.items()
            if not SECRET.search(re.sub(r"[^a-z]", "", str(key).lower()))
        }
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    if isinstance(value, str):
        stripped = value.lstrip()
        if stripped.startswith(("{", "[")):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, (dict, list)):
                    value = json.dumps(_safe_value(parsed), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            except (ValueError, RecursionError):
                pass
        return URL.sub(_redact_url, value)
    return value


def _literal(value) -> str:
    value = _safe_value(value)
    if value is None:
        text = ""
    elif isinstance(value, bool):
        text = "Yes" if value else "No"
    elif isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    else:
        text = str(value)
    # Keep every presentation cell below the Sheets UTF-16 limit.  The complete
    # source value remains available in Field details and the technical record.
    limit = 47_500
    if len(text.encode("utf-16-le")) // 2 > limit:
        suffix = "… See Backup - Field details for complete text."
        budget = limit - len(suffix.encode("utf-16-le")) // 2
        used = 0
        chars: list[str] = []
        for char in text:
            units = len(char.encode("utf-16-le")) // 2
            if used + units > budget:
                break
            chars.append(char)
            used += units
        text = "".join(chars) + suffix
    # RAW writes already prevent formula execution.  The leading apostrophe is
    # an additional invariant for receivers/tests that inspect the matrix before
    # it reaches Sheets.
    return "'" + text if text.startswith("=") else text


class _Grid:
    def __init__(self):
        self.rows: list[list[str]] = []
        self.width = 0

    def _ensure(self, row: int, col: int) -> None:
        self.width = max(self.width, col)
        while len(self.rows) < row:
            self.rows.append([])
        for values in self.rows:
            if len(values) < self.width:
                values.extend([""] * (self.width - len(values)))

    def cell(self, row: int, col: int, value) -> None:
        self._ensure(row, col)
        self.rows[row - 1][col - 1] = _literal(value)

    def block(self, start_row: int, start_col: int, rows: list[list[object]]) -> dict[str, int]:
        if not rows or not rows[0]:
            raise PresentationError("Presentation helper block cannot be empty.")
        width = len(rows[0])
        if any(len(row) != width for row in rows):
            raise PresentationError("Presentation helper block is not rectangular.")
        self._ensure(start_row + len(rows) - 1, start_col + width - 1)
        for row_offset, values in enumerate(rows):
            for col_offset, value in enumerate(values):
                self.rows[start_row - 1 + row_offset][start_col - 1 + col_offset] = _literal(value)
        return {
            "startRow": start_row,
            "endRow": start_row + len(rows) - 1,
            "startCol": start_col,
            "endCol": start_col + width - 1,
        }

    def finish(self) -> list[list[str]]:
        if not self.rows:
            raise PresentationError("Presentation grid is empty.")
        if self.width > MAX_PRESENTATION_COLUMNS:
            raise PresentationError("Presentation grid exceeds the 128-column limit.")
        self._ensure(len(self.rows), self.width)
        return self.rows


def _result_tab(payload: dict[str, object]) -> dict[str, object]:
    results = payload["results"]
    terms = payload["definition"]["terminology"]
    groups = Counter((row["scope"], row["view"]) for row in results["rows"])
    visible_capacity = max(groups.values(), default=1)
    grid = _Grid()
    grid.cell(1, 1, "Results & rankings")
    grid.cell(2, 1, "Completed Journees · overall, dimension, and activity rankings")
    grid.cell(3, 1, f"{terms['session']} view")
    grid.cell(3, 2, results["scopes"][0] if results["scopes"] else "")
    grid.cell(3, 4, "Result view")
    grid.cell(3, 5, results["views"][0] if results["views"] else "")
    headers = ["Rank", terms["participant"], terms["session"], "Score", "Scale", "Details", "Status", "Color", "General comment", "Notes"]
    for col, value in enumerate(headers, 1):
        grid.cell(5, col, value)
    grid._ensure(5 + visible_capacity, len(headers))

    counters: Counter[tuple[str, str]] = Counter()
    helper_rows: list[list[object]] = [[
        "Scope", "View", "Rank", terms["participant"], terms["session"], "Score", "Scale",
        "Details", "Status", "Color", "General comment", "Notes", "Lookup key",
    ]]
    for row in results["rows"]:
        group = (str(row["scope"]), str(row["view"]))
        counters[group] += 1
        helper_rows.append([
            row["scope"], row["view"], row["rank"], row["name"], row["journeyName"], row["score"],
            row["scale"], row["details"], row["status"], row["color"], row["generalComment"],
            row["notes"], f"{group[0]}|{group[1]}|{counters[group]}",
        ])
    blocks = {"results": grid.block(1, 13, helper_rows)}
    blocks["scopeOptions"] = grid.block(1, 26, [["Scope options"], *[[value] for value in results["scopes"]]])
    blocks["viewOptions"] = grid.block(1, 27, [["View options"], *[[value] for value in results["views"]]])
    rows = grid.finish()
    return {
        "name": "Results",
        "finalTitle": "Results",
        "presentation": "results-v1",
        "rows": rows,
        "layout": {
            "selectorCells": ["B3", "E3"],
            "headerRow": 5,
            "visibleStartRow": 6,
            "visibleCapacity": visible_capacity,
            "frozenRows": 5,
            "helperStartCol": 13,
            "tabColor": "GREEN",
            "columnWidths": [70, 210, 185, 95, 65, 155, 100, 90, 265, 265],
            "blocks": blocks,
        },
    }


def _placeholder_png() -> bytes:
    image = Image.new("RGB", (160, 160), "#F3F5F7")
    draw = ImageDraw.Draw(image)
    draw.ellipse((55, 28, 105, 78), fill="#C8102E")
    draw.rounded_rectangle((30, 84, 130, 140), radius=24, fill="#C8102E")
    draw.text((30, 145), "PHOTO NOT RECORDED", fill="#667085")
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _preview_png(encoded: str) -> bytes:
    if not isinstance(encoded, str) or len(encoded) > (MAX_PREVIEW_BYTES * 4 // 3 + 8):
        raise PresentationError("Profile preview is invalid or oversized.")
    try:
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > MAX_PREVIEW_BYTES:
            raise ValueError
        with Image.open(BytesIO(raw)) as image:
            image.verify()
        with Image.open(BytesIO(raw)) as image:
            if image.format != "PNG" or image.width > 512 or image.height > 512:
                raise ValueError
    except Exception:
        raise PresentationError("Profile preview is invalid or oversized.") from None
    return raw


def _profile_tab(payload: dict[str, object], photos: list[dict[str, str]]) -> dict[str, object]:
    profiles = payload["profiles"]
    definition = payload["definition"]
    terms = definition["terminology"]
    factors = profiles["generalFactors"]
    dimensions = profiles["dimensionDefinitions"]
    activities = profiles["activityDefinitions"]
    all_options = profiles["optionsByScope"].get(profiles["defaultScope"], [])
    photo_by_key = {
        f"{photo.get('journeyId', '')}:{photo.get('recruitId', '')}": photo
        for photo in photos
    }

    evaluator_counts = Counter(row["profileKey"] for row in profiles["evaluators"])
    criterion_counts = Counter(row["profileKey"] for row in profiles["criteria"])
    audit_counts = Counter(row["profileKey"] for row in profiles["audit"])
    evaluator_capacity = max(evaluator_counts.values(), default=1)
    criterion_capacity = min(116, max(criterion_counts.values(), default=1))
    audit_capacity = max(audit_counts.values(), default=1)
    dimension_section = 8
    dimension_header = 9
    dimension_start = 10
    activity_section = 26
    activity_header = 27
    activity_start = 28
    general_section = 44
    general_start = 45
    general_count = len(factors) + 4
    evaluator_section = max(55, general_start + general_count + 1)
    evaluator_header = evaluator_section + 1
    evaluator_start = evaluator_header + 1
    criterion_section = max(79, evaluator_start + evaluator_capacity + 1)
    criterion_header = criterion_section + 1
    criterion_start = criterion_header + 1
    audit_section = max(200, criterion_start + criterion_capacity + 1)
    audit_header = audit_section + 1
    audit_start = audit_header + 1

    grid = _Grid()
    grid.cell(1, 1, f"{terms['participant']} profile")
    grid.cell(2, 1, f"Complete view-only {str(terms['participant']).lower()} record")
    grid.cell(3, 1, f"{terms['session']} view")
    grid.cell(3, 2, profiles["defaultScope"])
    grid.cell(3, 4, terms["participant"])
    grid.cell(3, 5, profiles["defaultLabel"])
    for row, col, value in [
        (5, 1, f"{terms['participant']}:"), (5, 4, f"{terms['session']}:") , (5, 7, "Date:"),
        (6, 1, "Phone:"), (6, 4, "Date of birth:"), (6, 7, "Arrival:"),
        (7, 1, "Attendance:"), (7, 4, "Overall:"), (7, 7, "Color grade:"),
    ]:
        grid.cell(row, col, value)
    grid.cell(dimension_section, 1, "Dimension performance")
    for col, value in enumerate(["Dimension", "Score", "Rank", "Status", "Coverage"], 1):
        grid.cell(dimension_header, col, value)
    for offset, item in enumerate(dimensions):
        grid.cell(dimension_start + offset, 1, item["name"])
    grid.cell(activity_section, 1, "Activity performance")
    for col, value in enumerate([terms["stage"], "Score /5", "Rank", "Submissions", "Status"], 1):
        grid.cell(activity_header, col, value)
    for offset, item in enumerate(activities):
        grid.cell(activity_start + offset, 1, item["name"])
    grid.cell(general_section, 1, "General assessment and completion")
    general_labels = [
        *[f"{item['name']} /{float(item['maximum']):g}" for item in factors],
        "General average /1", "Missing components", "General comment", "Notes",
    ]
    for offset, value in enumerate(general_labels):
        grid.cell(general_start + offset, 1, value)
    grid.cell(evaluator_section, 1, f"{terms['assessor']} breakdown")
    for col, value in enumerate([terms["stage"], terms["assessor"], "Category", "Score /5", "Status", "Comment"], 1):
        grid.cell(evaluator_header, col, value)
    grid.cell(criterion_section, 1, "Criterion-level grading")
    for col, value in enumerate([terms["stage"], "Dimension", "Criterion", "Explanation", terms["assessor"], "Grade /5", "Raw result", "Status"], 1):
        grid.cell(criterion_header, col, value)
    grid.cell(audit_section, 1, "Profile audit history")
    for col, value in enumerate(["Date and time", "Username", "Action", "Reason", "Before", "After"], 1):
        grid.cell(audit_header, col, value)
    grid._ensure(audit_start + audit_capacity - 1, 12)

    summary_headers = [
        "Selection", "Profile key", terms["session"], "Date", terms["participant"], "Phone", "DOB",
        "Attendance", "Arrival", "Attendance comment", "Overall", "Display rank", "Overall rank",
        "Overall population", "Journee rank", "Journee population", "Color", "Missing",
        *[item["name"] for item in factors], "General average", "General comment", "Notes",
    ]
    summary_rows = [summary_headers]
    for row in profiles["summaries"]:
        summary_rows.append([
            row["selectionKey"], row["profileKey"], row["journeyName"], row["journeyDate"], row["name"],
            row["phoneNumber"], row["dateOfBirth"], row["attendance"], row["arrivalTime"],
            row["attendanceComment"], row["overallScore"], row["displayRank"], row["overallRank"],
            row["overallPopulation"], row["journeyRank"], row["journeyPopulation"], row["color"],
            row["missingComponents"],
            *[row["generalValues"].get(item["storageKey"]) for item in factors],
            row["generalAverage"], row["generalComment"], row["notes"],
        ])
    dimension_rows = [["Selection", "Dimension", "Score", "Rank", "Status", "Coverage"], *[
        [row["selectionKey"], row["name"], row["score"], row["rank"], row["status"], row["coverage"]]
        for row in profiles["dimensions"]
    ]]
    activity_rows = [["Selection", "Activity", "Score", "Rank", "Submissions", "Status"], *[
        [row["selectionKey"], row["name"], row["score"], row["rank"], row["submissions"], row["status"]]
        for row in profiles["activities"]
    ]]
    evaluator_rows = [["Profile key", terms["stage"], terms["assessor"], "Category", "Score", "Status", "Comment"], *[
        [row["profileKey"], row["activity"], row["evaluator"], row["category"], row["score"], row["status"], row["comment"]]
        for row in profiles["evaluators"]
    ]]
    criterion_rows = [["Profile key", terms["stage"], "Dimension", "Criterion", "Explanation", terms["assessor"], "Grade", "Raw result", "Status"], *[
        [row["profileKey"], row["activity"], row["dimension"], row["criterion"], row["explanation"], row["evaluator"], row["grade"], row["rawResult"], row["status"]]
        for row in profiles["criteria"]
    ]]
    audit_rows = [["Profile key", "Date", "Username", "Action", "Reason", "Before", "After"], *[
        [row["profileKey"], row["createdAt"], row["actorName"], row["action"], row["reason"], row["before"], row["after"]]
        for row in profiles["audit"]
    ]]
    option_rows = [["Scope", "Label", "Profile key"]]
    for scope in profiles["scopes"]:
        option_rows.extend([[scope, option["label"], option["profileKey"]] for option in profiles["optionsByScope"].get(scope, [])])

    preview_rows: list[list[object]] = [["Profile key", "Part", "Parts", "SHA-256", "PNG chunk"]]
    seen_keys: set[str] = set()
    placeholder = _placeholder_png()
    for option in all_options:
        profile_key = str(option["profileKey"])
        if profile_key in seen_keys:
            raise PresentationError("Duplicate profile key in presentation options.")
        seen_keys.add(profile_key)
        photo = photo_by_key.get(profile_key)
        raw = _preview_png(photo["preview"]) if photo else placeholder
        encoded = base64.b64encode(raw).decode("ascii")
        parts = [encoded[index:index + CHUNK_SIZE] for index in range(0, len(encoded), CHUNK_SIZE)]
        digest = sha256(raw).hexdigest()
        preview_rows.extend([
            [profile_key, str(index), str(len(parts)), digest, chunk]
            for index, chunk in enumerate(parts)
        ])

    blocks: dict[str, dict[str, int]] = {}
    next_col = 27
    for name, values in (
        ("summaries", summary_rows), ("dimensions", dimension_rows), ("activities", activity_rows),
        ("evaluators", evaluator_rows), ("criteria", criterion_rows), ("audit", audit_rows),
        ("profileOptions", option_rows), ("previews", preview_rows),
    ):
        blocks[name] = grid.block(1, next_col, values)
        next_col = blocks[name]["endCol"] + 1
    rows = grid.finish()
    if seen_keys != {str(option["profileKey"]) for option in all_options}:
        raise PresentationError("Profile preview coverage is incomplete.")
    return {
        "name": "Recruit Profiles",
        "finalTitle": "Recruit Profiles",
        "presentation": "recruit-profiles-v1",
        "rows": rows,
        "layout": {
            "selectorCells": ["B3", "E3"],
            "profileKeyCell": "H3",
            "imageAnchor": "J3",
            "frozenRows": 7,
            "helperStartCol": 27,
            "tabColor": "YELLOW",
            "columnWidths": [165, 125, 24, 145, 125, 24, 120, 125, 90, 75, 75, 75],
            "expectedPreviewCount": len(seen_keys),
            "dimensionCount": len(dimensions),
            "activityCount": len(activities),
            "factorCount": len(factors),
            "evaluatorCapacity": evaluator_capacity,
            "criterionCapacity": criterion_capacity,
            "auditCapacity": audit_capacity,
            "sectionRows": {
                "dimension": dimension_section, "dimensionHeader": dimension_header, "dimensionStart": dimension_start,
                "activity": activity_section, "activityHeader": activity_header, "activityStart": activity_start,
                "general": general_section, "generalStart": general_start,
                "evaluator": evaluator_section, "evaluatorHeader": evaluator_header, "evaluatorStart": evaluator_start,
                "criterion": criterion_section, "criterionHeader": criterion_header, "criterionStart": criterion_start,
                "audit": audit_section, "auditHeader": audit_header, "auditStart": audit_start,
            },
            "charts": [
                {"startRow": dimension_start, "endRow": dimension_start + len(dimensions) - 1, "labelCol": 1, "valueCol": 2, "anchor": "G8", "maximum": int(float(dimensions[0]["displayMaximum"])) if dimensions else 5},
                {"startRow": activity_start, "endRow": activity_start + len(activities) - 1, "labelCol": 1, "valueCol": 2, "anchor": "G26", "maximum": 5},
            ],
            "blocks": blocks,
        },
    }


def build_presentation_tabs(
    payload: dict[str, object],
    photos: list[dict[str, str]],
) -> list[dict[str, object]]:
    tabs = [_result_tab(payload), _profile_tab(payload, photos)]
    for tab in tabs:
        rows = tab["rows"]
        if not rows or len(rows[0]) > MAX_PRESENTATION_COLUMNS or len({len(row) for row in rows}) != 1:
            raise PresentationError("Presentation grid is outside the bounded rectangular contract.")
        if any(not isinstance(cell, str) or cell.startswith("=") for row in rows for cell in row):
            raise PresentationError("Presentation values must be literal strings.")
    return tabs


def layout_operation(tab: dict[str, object]) -> dict[str, object]:
    if tab.get("presentation") not in {"results-v1", "recruit-profiles-v1"}:
        raise PresentationError("Unknown presentation template.")
    return {
        "kind": "layout",
        "version": 1,
        "tab": tab["name"],
        "presentation": tab["presentation"],
        "layout": tab["layout"],
    }
