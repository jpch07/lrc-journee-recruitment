from __future__ import annotations

import json
from decimal import Decimal

from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy import create_engine, text

from app.assessment_config import AssessmentSystemDefinition, lrc_assessment_definition
from app.assessment_runtime import activate_assessment_definition, reset_assessment_definition
from app.db import SessionLocal
from app.general_assessment import set_general_assessment_values, stored_general_assessment_values
from app.models import AuditEvent, GeneralAssessment, Journey, Recruit
from app.report_exports import build_management_report_workbook, management_report_filename
from app.routes_admin import _full_export, recruit_profile, save_general_assessment
from app.schemas import GeneralAssessmentRequest
from app.services import result_snapshot
from app.utils import loads


def _custom_definition() -> AssessmentSystemDefinition:
    payload = lrc_assessment_definition().model_dump(mode="json")
    payload["name"] = "Generic Selection"
    payload["terminology"].update({
        "participant": "Applicant",
        "participantPlural": "Applicants",
        "assessor": "Reviewer",
        "assessorPlural": "Reviewers",
        "session": "Round",
        "sessionPlural": "Rounds",
        "group": "Panel",
        "groupPlural": "Panels",
        "stage": "Interview",
        "stagePlural": "Interviews",
    })
    payload["scoring"]["officialMaximum"] = "50"
    for dimension in payload["dimensions"]:
        dimension["displayMaximum"] = "10"
    payload["generalFactors"] = [
        {"storageKey": "communication", "name": "Communication", "maximum": "5", "step": "0.5"},
        {"storageKey": "motivation", "name": "Motivation", "maximum": "1", "step": "0.1"},
    ]
    return AssessmentSystemDefinition.model_validate(payload)


def _create_recruit(client) -> tuple[str, str]:
    login = client.post(
        "/api/auth/login", json={"username": "JP Chaaya", "password": "test-password"}
    ).json()
    headers = {"X-CSRF-Token": login["csrfToken"]}
    journey = client.post(
        "/api/admin/journeys",
        headers=headers,
        json={"name": "Dynamic factors", "event_date": "2026-09-09"},
    ).json()
    recruit = client.post(
        f"/api/admin/journeys/{journey['id']}/recruits",
        headers=headers,
        json={"name": "Configurable Candidate"},
    ).json()
    return journey["id"], recruit["id"]


def test_dynamic_general_factors_drive_profile_results_audit_and_exports(client):
    journey_id, recruit_id = _create_recruit(client)
    definition = _custom_definition()
    tokens = activate_assessment_definition(definition)
    try:
        with SessionLocal() as db:
            journey = db.get(Journey, journey_id)
            recruit = db.get(Recruit, recruit_id)
            journey.status = "completed"
            recruit.present = True
            db.commit()

            saved = save_general_assessment(
                db,
                journey_id=journey_id,
                recruit_id=recruit_id,
                payload=GeneralAssessmentRequest(
                    values={"communication": 4.0, "motivation": 1.0},
                    comment="Strong interview",
                    notes="Follow up",
                    base_version=0,
                ),
                actor_name="Owner",
                actor_type="admin",
            )
            assert saved["values"] == {"communication": 4.0, "motivation": 1.0}

            assessment = db.get(GeneralAssessment, recruit_id)
            assert loads(assessment.values_json, {}) == {
                "communication": 4.0,
                "motivation": 1.0,
            }
            assert assessment.punctuality is None
            assert assessment.respect is None
            assert assessment.seriousness is None

            result = result_snapshot(db, journey)
            row = next(item for item in result["rows"] if item["recruitId"] == recruit_id)
            assert row["generalAverage"] == 0.9

            profile = recruit_profile(journey_id, recruit_id, context=None, db=db)
            assert profile["assessment"]["values"] == {
                "communication": 4.0,
                "motivation": 1.0,
            }
            assert profile["assessment"]["punctuality"] is None

            event = db.query(AuditEvent).filter(AuditEvent.entity_id == recruit_id).order_by(
                AuditEvent.created_at.desc()
            ).first()
            after = loads(event.after_json, {})
            assert after["factors"] == [
                {"key": "communication", "name": "Communication", "value": 4.0},
                {"key": "motivation", "name": "Motivation", "value": 1.0},
            ]

            raw_export = _full_export(db, journey)
            guide_sheet = raw_export["Scoring Guide"]
            assert [cell.value for cell in guide_sheet[1]][:4] == [
                "Interview", "Dimension", "Criterion", "Weight within dimension (%)"
            ]
            assert 0 < guide_sheet.cell(2, 4).value <= 100
            duration_rows = [
                row for row in guide_sheet.iter_rows(min_row=2, values_only=True)
                if row[5] == "duration"
            ]
            assert duration_rows
            assert duration_rows[0][6].count(":") == 2

            results_sheet = raw_export["Results"]
            results_headers = [cell.value for cell in results_sheet[1]]
            assert results_headers[1] == "Applicant"
            assert results_headers[2] == "Overall /50"
            assert all(
                f"{dimension.name} /10" in results_headers
                for dimension in definition.dimensions
            )

            general_sheet = raw_export["General assessments"]
            assert [cell.value for cell in general_sheet[1]] == [
                "Applicant", "Communication /5", "Motivation /1", "Comment", "Notes", "Version"
            ]
            assert [cell.value for cell in general_sheet[2]][1:3] == [4.0, 1.0]

            report = build_management_report_workbook(db)
            assert report["Attendance"]["B3"].value == "All completed Rounds"
            assert report["Results"]["B3"].value == "All completed Rounds"
            profile_sheet = report["Recruit Profiles"]
            assert profile_sheet["B3"].value == "All completed Rounds"
            assert management_report_filename() == "Generic-Selection-management-report.xlsx"
            visible_labels = {profile_sheet.cell(row, 1).value for row in range(44, 60)}
            assert "Communication /5" in visible_labels
            assert "Motivation /1" in visible_labels

            updated = save_general_assessment(
                db,
                journey_id=journey_id,
                recruit_id=recruit_id,
                payload=GeneralAssessmentRequest(
                    values={"communication": 4.5},
                    comment="Strong interview",
                    notes="Follow up",
                    base_version=saved["version"],
                ),
                actor_name="Owner",
                actor_type="admin",
            )
            assert updated["values"] == {"communication": 4.5, "motivation": 1.0}

            try:
                save_general_assessment(
                    db,
                    journey_id=journey_id,
                    recruit_id=recruit_id,
                    payload=GeneralAssessmentRequest(values={"unknown_factor": 1}),
                    actor_name="Owner",
                    actor_type="admin",
                )
            except HTTPException as exc:
                assert exc.status_code == 422
            else:
                raise AssertionError("Unknown dynamic factors must be rejected.")
    finally:
        reset_assessment_definition(tokens)


def test_large_custom_factor_value_does_not_overflow_legacy_columns():
    assessment = GeneralAssessment(recruit_id="test-recruit")

    set_general_assessment_values(assessment, {"punctuality": Decimal("75")})

    assert assessment.punctuality is None
    assert stored_general_assessment_values(assessment)["punctuality"] == Decimal("75")


def test_dynamic_factor_migration_backfills_legacy_columns(tmp_path):
    database_path = tmp_path / "legacy-general-assessment.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0017_room_evaluator_locks")
        # 0001 uses current metadata for clean installations, so remove the new
        # column to reproduce an actual database that stopped at revision 0017.
        connection.execute(text("ALTER TABLE general_assessments DROP COLUMN values_json"))
        connection.execute(text(
            "INSERT INTO general_assessments "
            "(recruit_id, punctuality, respect, seriousness, comment, notes, version, updated_at) "
            "VALUES ('legacy-recruit', 0.7, 0.8, 0.9, 'legacy', '', 1, CURRENT_TIMESTAMP)"
        ))
        command.upgrade(config, "head")
        row = connection.execute(text(
            "SELECT punctuality, respect, seriousness, values_json FROM general_assessments"
        )).mappings().one()
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

    assert json.loads(row["values_json"]) == {
        "punctuality": 0.7,
        "respect": 0.8,
        "seriousness": 0.9,
    }
    assert revision == "0018_dynamic_general_factors"
