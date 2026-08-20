from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.assessment_config import blank_assessment_definition, lrc_assessment_definition
from app.assessment_runtime import activate_assessment_definition
from app.assessment_service import ensure_assessment_system, publish_definition
from app.db import SessionLocal
from app.models import ActivityState, Journey
from app.scoring import target_activity_score
from app.services import create_journey


def _owner_login(client):
    response = client.post("/api/auth/login", json={"username": "JP Chaaya", "password": "test-password"})
    assert response.status_code == 200, response.text
    return response.json()


def test_lrc_preset_exactly_preserves_the_finalized_system():
    definition = lrc_assessment_definition()
    assert [item.key for item in definition.activities] == [
        "sport", "escape_room", "negotiation", "skills", "simulation"
    ]
    assert [item.key for item in definition.assessors.categories] == ["overall", "dossard"]
    assert definition.activities[4].assignment.reuseAssignmentsFrom == "skills"
    assert definition.activities[3].assignment.parallelGroup == "skills_simulation"
    assert definition.activities[4].assignment.parallelGroup == "skills_simulation"
    assert definition.scoring.officialMaximum == Decimal("20")
    assert sum((item.weight for item in definition.scoring.components), Decimal("0")) == Decimal("1")


def test_owner_can_publish_a_cosmetic_configuration_without_changing_saved_sessions(client):
    owner = _owner_login(client)
    headers = {"X-CSRF-Token": owner["csrfToken"]}
    journey = client.post("/api/admin/journeys", headers=headers,
                          json={"name": "Compatibility", "event_date": "2026-09-01"})
    assert journey.status_code == 200, journey.text
    payload = client.get("/api/configurator").json()
    definition = deepcopy(payload["draft"])
    definition["branding"]["primaryColor"] = "#315be8"
    published = client.post("/api/configurator/publish", headers=headers, json={
        "definition": definition,
        "base_version": payload["system"]["version"],
        "change_summary": "Cosmetic compatibility test",
    })
    assert published.status_code == 200, published.text
    public = client.get("/api/configurator/public").json()
    assert public["branding"]["primaryColor"] == "#315be8"
    assert [item["key"] for item in public["activities"]] == [
        "sport", "escape_room", "negotiation", "skills", "simulation"
    ]


def test_structural_publish_is_blocked_when_sessions_exist(client):
    owner = _owner_login(client)
    headers = {"X-CSRF-Token": owner["csrfToken"]}
    client.post("/api/admin/journeys", headers=headers,
                json={"name": "Protected history", "event_date": "2026-09-02"})
    payload = client.get("/api/configurator").json()
    definition = deepcopy(payload["draft"])
    definition["activities"][1]["criteria"][0]["weight"] = "0.16"
    response = client.post("/api/configurator/publish", headers=headers, json={
        "definition": definition,
        "base_version": payload["system"]["version"],
        "change_summary": "Must be protected",
    })
    assert response.status_code == 409
    assert "saved Sessions exist" in response.json()["detail"]


def test_blank_preset_drives_the_existing_session_runtime():
    lrc = lrc_assessment_definition()
    try:
        with SessionLocal() as db:
            system = ensure_assessment_system(db)
            blank = blank_assessment_definition()
            publish_definition(
                db, system, blank, base_version=system.version,
                actor_name="Test owner", change_summary="Blank local system",
            )
            journey = create_journey(db, "Generic session", date(2026, 9, 3), 1, "Test owner")
            db.commit()
            states = list(db.scalars(select(ActivityState).where(ActivityState.journey_id == journey.id)))
            assert [item.code for item in states] == ["evaluation"]
    finally:
        activate_assessment_definition(lrc)


def test_target_scoring_supports_lower_is_better():
    lrc = lrc_assessment_definition()
    definition = blank_assessment_definition()
    activity = definition.activities[0]
    activity.scoring = "target_average"
    activity.criteria[0].inputType = "duration"
    activity.criteria[0].target = Decimal("10")
    activity.criteria[0].direction = "lower"
    try:
        activate_assessment_definition(definition)
        at_target, *_ = target_activity_score("evaluation", {"performance": "10"})
        slower, *_ = target_activity_score("evaluation", {"performance": "20"})
        faster, *_ = target_activity_score("evaluation", {"performance": "5"})
        assert at_target == Decimal("5.00")
        assert slower == Decimal("2.50")
        assert faster == Decimal("5.00")
    finally:
        activate_assessment_definition(lrc)
