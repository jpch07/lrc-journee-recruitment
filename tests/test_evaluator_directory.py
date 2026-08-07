from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.db import SessionLocal
from app.evaluator_master import BASE_EVALUATORS
from app.models import Evaluator, EvaluatorDirectory
from app.services import create_journey


def _login(client) -> str:
    response = client.post(
        "/api/admin/login",
        json={"password": "test-password", "display_name": "Directory Test"},
    )
    assert response.status_code == 200, response.text
    return response.json()["csrfToken"]


def test_authoritative_evaluator_workbook_snapshot_is_clean():
    assert len(BASE_EVALUATORS) == 82
    assert len({name.casefold() for name, _ in BASE_EVALUATORS}) == 82
    assert sum(role == "overall" for _, role in BASE_EVALUATORS) == 67
    assert sum(role == "dossard" for _, role in BASE_EVALUATORS) == 15
    assert {role for _, role in BASE_EVALUATORS} == {"overall", "dossard"}


def test_new_journey_contains_every_active_directory_evaluator_as_absent():
    with SessionLocal() as db:
        for name, role in BASE_EVALUATORS:
            db.add(EvaluatorDirectory(name=name, default_role=role))
        db.flush()
        journey = create_journey(db, "Directory Journey", date(2026, 8, 8), 1, "Test")
        db.commit()
        evaluators = list(
            db.scalars(select(Evaluator).where(Evaluator.journey_id == journey.id))
        )

    assert len(evaluators) == 82
    assert not any(item.present for item in evaluators)
    assert {(item.name, item.role) for item in evaluators} == set(BASE_EVALUATORS)


def test_custom_evaluator_becomes_a_default_for_future_journeys(client):
    csrf = _login(client)
    headers = {"X-CSRF-Token": csrf}
    first = client.post(
        "/api/admin/journeys",
        headers=headers,
        json={"name": "First", "event_date": "2026-08-08", "room_count": 1},
    ).json()
    response = client.post(
        f"/api/admin/journeys/{first['id']}/evaluators",
        headers=headers,
        json={"name": "Visiting Evaluator", "role": "dossard", "add_to_directory": True, "password": "temporary-pass"},
    )
    assert response.status_code == 200, response.text

    second = client.post(
        "/api/admin/journeys",
        headers=headers,
        json={"name": "Second", "event_date": "2026-08-09", "room_count": 1},
    ).json()
    detail = client.get(f"/api/admin/journeys/{second['id']}").json()
    custom = next(item for item in detail["evaluators"] if item["name"] == "Visiting Evaluator")
    assert custom["role"] == "dossard"
    assert custom["present"] is False


def test_mandatory_room_selection_persists_for_absent_directory_evaluator(client):
    csrf = _login(client)
    headers = {"X-CSRF-Token": csrf}
    with SessionLocal() as db:
        db.add(EvaluatorDirectory(name="Absent Overall", default_role="overall"))
        db.commit()
    journey = client.post(
        "/api/admin/journeys",
        headers=headers,
        json={"name": "Rooms", "event_date": "2026-08-10", "room_count": 2},
    ).json()
    detail = client.get(f"/api/admin/journeys/{journey['id']}").json()
    evaluator = next(item for item in detail["evaluators"] if item["name"] == "Absent Overall")

    response = client.put(
        f"/api/admin/journeys/{journey['id']}/mandatory-rooms",
        headers=headers,
        json={"items": [{"evaluator_id": evaluator["id"], "room_number": 1}]},
    )
    assert response.status_code == 200, response.text
    refreshed = client.get(f"/api/admin/journeys/{journey['id']}").json()
    persisted = next(item for item in refreshed["evaluators"] if item["id"] == evaluator["id"])
    assert persisted["mandatoryRoom"] == 1
