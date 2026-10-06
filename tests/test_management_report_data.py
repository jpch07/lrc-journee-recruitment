from __future__ import annotations

import json
from datetime import date

from app.db import SessionLocal
from app.models import Journey, Recruit
from app.services import create_journey
from app.management_report_payload import (
    build_management_report_payload,
    load_management_report_source,
)
from app.report_exports import build_management_report_workbook_from_payload


def _completed_report_fixture():
    with SessionLocal() as db:
        older = create_journey(db, "Completed Alpha", date(2026, 8, 1), 1, "Test")
        newer = create_journey(db, "Completed Bravo", date(2026, 8, 2), 1, "Test")
        active = create_journey(db, "Active day", date(2026, 8, 3), 1, "Test")
        older.status = newer.status = "completed"
        active.status = "active"
        db.add_all([
            Recruit(journey_id=older.id, name="Duplicate Recruit", present=True),
            Recruit(journey_id=older.id, name="Absent Recruit", present=False),
            Recruit(journey_id=newer.id, name="Duplicate Recruit", present=True),
            Recruit(journey_id=active.id, name="Active Only", present=True),
        ])
        db.commit()
        return older.id, newer.id


def test_payload_uses_completed_journees_and_deterministic_rank_order():
    _completed_report_fixture()
    with SessionLocal() as db:
        source = load_management_report_source(db)
    payload = build_management_report_payload(source)

    assert payload["results"]["scopes"][0] == "All completed Journees"
    assert "Active day" not in payload["results"]["scopes"]
    overall = [
        row for row in payload["results"]["rows"]
        if row["scope"] == "All completed Journees" and row["view"] == "Overall ranking"
    ]
    assert [(row["rank"], row["name"], row["journeyName"]) for row in overall] == sorted(
        [(row["rank"], row["name"], row["journeyName"]) for row in overall],
        key=lambda item: (
            item[0] in (None, ""),
            item[0] or 10**9,
            item[1].casefold(),
            item[2].casefold(),
        ),
    )


def test_payload_profiles_are_complete_unique_and_json_serializable():
    older_id, newer_id = _completed_report_fixture()
    with SessionLocal() as db:
        source = load_management_report_source(db)
    payload = build_management_report_payload(source)

    all_options = payload["profiles"]["optionsByScope"]["All completed Journees"]
    assert any(option["name"] == "Absent Recruit" for option in all_options)
    duplicate_labels = [option["label"] for option in all_options if option["name"] == "Duplicate Recruit"]
    assert len(duplicate_labels) == len(set(duplicate_labels)) == 2
    assert all("—" in label and "·" in label for label in duplicate_labels)
    assert {option["profileKey"].split(":", 1)[0] for option in all_options} == {older_id, newer_id}

    summaries = payload["profiles"]["summaries"]
    assert summaries
    assert all("overallRank" in row and "overallPopulation" in row for row in summaries)
    assert all("journeyRank" in row and "journeyPopulation" in row for row in summaries)
    json.dumps(payload, ensure_ascii=False, allow_nan=False)


def test_management_source_is_detached_from_later_database_mutation():
    _completed_report_fixture()
    with SessionLocal() as db:
        source = load_management_report_source(db)

    with SessionLocal() as db:
        recruit = db.query(Recruit).filter(Recruit.name == "Absent Recruit").one()
        recruit.name = "Mutated After Snapshot"
        db.commit()

    payload = build_management_report_payload(source)
    names = {row["name"] for row in payload["profiles"]["summaries"]}
    assert "Absent Recruit" in names
    assert "Mutated After Snapshot" not in names


def test_excel_renderer_consumes_detached_payload():
    _completed_report_fixture()
    with SessionLocal() as db:
        payload = build_management_report_payload(load_management_report_source(db))

    workbook = build_management_report_workbook_from_payload(payload)
    assert workbook.sheetnames == ["Attendance", "Results", "Recruit Profiles"]
    assert workbook["Attendance"]["B3"].value == "All completed Journees"
    assert workbook["Results"]["E3"].value == "Overall ranking"
    assert "XLOOKUP" in workbook["Recruit Profiles"]["H3"].value
    assert len(workbook["Recruit Profiles"]._charts) == 2
    workbook.close()
