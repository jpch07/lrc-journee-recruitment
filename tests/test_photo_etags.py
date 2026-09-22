from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app import routes_admin, routes_attendance, routes_evaluator, routes_viewer
from app.db import SessionLocal
from app.models import ActivityState, Assignment, AssignmentRound, Evaluator, Recruit
from app.services import create_journey


@pytest.mark.parametrize("surface", ["admin", "attendance", "evaluator", "viewer"])
def test_photo_etag_requires_authorization_and_skips_body_read(client, monkeypatch, surface):
    digest = "a" * 64
    with SessionLocal() as db:
        journey = create_journey(db, "Photo cache test", date(2026, 9, 22), 1, "Test")
        journey.status = "active"
        recruit = Recruit(
            journey_id=journey.id, name="Photo Recruit", present=True,
            photo_object_key="test-only/photo.webp", photo_size=4,
            photo_sha256=digest, photo_type="image/webp",
        )
        evaluator = db.scalar(select(Evaluator).where(Evaluator.journey_id == journey.id, Evaluator.name.ilike("JP Chaaya")))
        if evaluator is None:
            evaluator = Evaluator(journey_id=journey.id, name="JP Chaaya", role="dossard")
        evaluator.present = True
        another = Evaluator(journey_id=journey.id, name="Other Assessor", role="overall", present=True)
        db.add_all([recruit, evaluator, another])
        db.flush()
        round_record = AssignmentRound(
            journey_id=journey.id, activity_code="sport", version=1,
            status="published", seed="photo-test", created_by="Test",
        )
        db.add(round_record)
        db.flush()
        task = Assignment(round_id=round_record.id, evaluator_id=evaluator.id, recruit_id=recruit.id, slot=1)
        other_task = Assignment(round_id=round_record.id, evaluator_id=another.id, recruit_id=recruit.id, slot=2)
        db.add_all([task, other_task])
        state = db.scalar(select(ActivityState).where(ActivityState.journey_id == journey.id, ActivityState.code == "sport"))
        state.assignment_round_id = round_record.id
        state.status = "open"
        db.commit()
        journey_id, recruit_id, task_id, other_task_id = journey.id, recruit.id, task.id, other_task.id

    modules = {"admin": routes_admin, "attendance": routes_attendance, "evaluator": routes_evaluator, "viewer": routes_viewer}
    urls = {
        "admin": f"/api/admin/journeys/{journey_id}/recruits/{recruit_id}/photo",
        "attendance": f"/api/recruit-attendance/recruits/{recruit_id}/photo?journey_id={journey_id}",
        "evaluator": f"/api/evaluator/tasks/{task_id}/photo",
        "viewer": f"/api/view/journeys/{journey_id}/recruits/{recruit_id}/photo",
    }
    url = urls[surface]
    reads = []

    def read_body(person):
        reads.append(person.id)
        return b"test"

    monkeypatch.setattr(modules[surface], "read_recruit_photo", read_body)
    match = {"If-None-Match": f'"{digest}"'}
    anonymous = client.get(url, headers=match)
    assert anonymous.status_code == 401
    assert anonymous.headers["cache-control"] == "no-store"
    assert reads == []

    login = client.post("/api/auth/login", json={"username": "JP Chaaya", "password": "test-password"})
    assert login.status_code == 200, login.text
    assert login.headers["cache-control"] == "no-store"
    cached = client.get(url, headers=match)
    assert cached.status_code == 304, cached.text
    assert cached.content == b""
    assert cached.headers["etag"] == match["If-None-Match"]
    assert cached.headers["cache-control"] == "private, max-age=86400"
    assert reads == [], "A valid conditional request must not fetch R2 or deferred photo bytes."

    forbidden_url = url.replace(task_id, other_task_id) if surface == "evaluator" else url.replace(recruit_id, "missing-recruit")
    forbidden = client.get(forbidden_url, headers=match)
    assert forbidden.status_code == 404
    assert forbidden.headers["cache-control"] == "no-store"
    assert reads == [], "A matching digest must not bypass task or recruit scope checks."

    fresh = client.get(url, headers={"If-None-Match": '"different-digest"'})
    assert fresh.status_code == 200
    assert fresh.content == b"test"
    assert fresh.headers["cache-control"] == "private, max-age=86400"
    assert fresh.headers["content-type"] == "image/webp"
    assert reads == [recruit_id]

    # Legacy records without authoritative checksums still fetch their actual body.
    with SessionLocal() as db:
        db.get(Recruit, recruit_id).photo_sha256 = None
        db.commit()
    legacy = client.get(url, headers=match)
    assert legacy.status_code == 200
    assert "etag" not in legacy.headers
    assert reads == [recruit_id, recruit_id]
    monkeypatch.setattr(modules[surface], "read_recruit_photo", lambda person: None)
    assert client.get(url, headers=match).status_code == 404
