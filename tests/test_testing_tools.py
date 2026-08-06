from __future__ import annotations

import random
from datetime import date

from sqlalchemy import func, select

from app.config import Settings
from app.db import SessionLocal
from app.models import (
    ActivityState,
    Assignment,
    AssignmentRound,
    EvaluationSubmission,
    Evaluator,
    Journey,
    Recruit,
    SubmissionVersion,
)
from app.routes_admin import _simulated_evaluation_values
from app.rubric import RUBRICS
from app.services import create_journey, score_evaluation


def _login(client) -> str:
    response = client.post(
        "/api/admin/login",
        json={"password": "test-password", "display_name": "Simulator Test"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["testToolsEnabled"] is True
    return response.json()["csrfToken"]


def _open_sport_with_three_tasks() -> str:
    with SessionLocal() as db:
        journey = create_journey(db, "Simulation Tool Test", date(2026, 11, 1), 1, "Test")
        recruits = [Recruit(journey_id=journey.id, name=f"Recruit {index}", present=True) for index in range(3)]
        evaluators = [Evaluator(journey_id=journey.id, name=f"Evaluator {index}", role="overall", present=True) for index in range(3)]
        db.add_all([*recruits, *evaluators])
        db.flush()
        round_record = AssignmentRound(
            journey_id=journey.id,
            activity_code="sport",
            version=1,
            status="published",
            seed="testing-tool",
            created_by="Test",
        )
        db.add(round_record)
        db.flush()
        for recruit, evaluator in zip(recruits, evaluators, strict=True):
            db.add(Assignment(
                round_id=round_record.id,
                evaluator_id=evaluator.id,
                recruit_id=recruit.id,
                slot=1,
            ))
        state = db.scalar(select(ActivityState).where(
            ActivityState.journey_id == journey.id,
            ActivityState.code == "sport",
        ))
        state.status = "open"
        state.assignment_round_id = round_record.id
        journey.status = "active"
        journey.current_activity = "sport"
        db.commit()
        return journey.id


def test_simulator_completes_exact_count_then_all_remaining(client):
    csrf = _login(client)
    journey_id = _open_sport_with_three_tasks()
    url = f"/api/admin/journeys/{journey_id}/testing/simulate/sport"
    headers = {"X-CSRF-Token": csrf}

    too_many = client.post(url, headers=headers, json={"count": 4})
    assert too_many.status_code == 422

    idempotent_headers = {**headers, "Idempotency-Key": "simulator-exact-one"}
    first = client.post(url, headers=idempotent_headers, json={"count": 1})
    assert first.status_code == 200, first.text
    assert first.json()["completedCount"] == 1
    assert first.json()["remainingCount"] == 2
    duplicate = client.post(url, headers=idempotent_headers, json={"count": 1})
    assert duplicate.status_code == 200
    assert duplicate.json() == first.json()

    rest = client.post(url, headers=headers, json={})
    assert rest.status_code == 200, rest.text
    assert rest.json()["completedCount"] == 2
    assert rest.json()["remainingCount"] == 0

    with SessionLocal() as db:
        submissions = list(db.scalars(select(EvaluationSubmission)))
        evaluators = {item.id: item.name for item in db.scalars(select(Evaluator))}
        versions = list(db.scalars(select(SubmissionVersion)))
        assert len(submissions) == 3
        assert all(item.status == "submitted" for item in submissions)
        assert all(item.comments.startswith("Automated test evaluation") for item in submissions)
        assert {item.actor_name for item in versions} == {evaluators[item.evaluator_id] for item in submissions}
        assert db.scalar(select(func.count()).select_from(SubmissionVersion)) == 3

    complete = client.post(url, headers=headers, json={})
    assert complete.status_code == 409


def test_simulator_requires_activity_to_be_open(client):
    csrf = _login(client)
    journey_id = _open_sport_with_three_tasks()
    with SessionLocal() as db:
        state = db.scalar(select(ActivityState).where(
            ActivityState.journey_id == journey_id,
            ActivityState.code == "sport",
        ))
        state.status = "closed"
        db.commit()
    response = client.post(
        f"/api/admin/journeys/{journey_id}/testing/simulate/sport",
        headers={"X-CSRF-Token": csrf},
        json={"count": 1},
    )
    assert response.status_code == 409


def test_simulated_payloads_are_valid_for_every_activity():
    rng = random.Random(203)
    for code in RUBRICS:
        responses, raw = _simulated_evaluation_values(code, rng)
        score, _normalized_responses, _normalized_raw = score_evaluation(code, responses, raw)
        assert 0 <= score <= 5


def test_testing_tools_cannot_be_enabled_in_production():
    production = Settings(
        database_url="sqlite://",
        admin_password_hash="",
        admin_password="test",
        session_secret="test",
        cookie_secure=True,
        session_hours=1,
        environment="production",
        test_tools_enabled=True,
    )
    assert production.allow_test_tools is False
