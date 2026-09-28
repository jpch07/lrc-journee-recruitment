"""C1 regression checks using only fictional, local test data.

Replaces the earlier C1 test module. Application code is unchanged.
These tests require the repository's existing tests/conftest.py.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import event, select

from app.db import SessionLocal, engine
from app.models import (
    ActivityState,
    AssessmentSystem,
    AssessmentSystemVersion,
    Assignment,
    AssignmentRound,
    AuditEvent,
    EvaluationSubmission,
    Evaluator,
    GeneralAssessment,
    Recruit,
    SubmissionVersion,
)
from app.routes_viewer import _completed_results, _completed_view
from app.rubric import RUBRICS
from app.services import create_journey
from app.tenant import reset_system, select_system
from app.utils import dumps


# Fail before fixtures run if this module is pointed at anything except the
# disposable database explicitly selected by this repository's conftest.py.
_expected_db = Path(__file__).with_name("journee-test.db").resolve()
if (
    engine.url.get_backend_name() != "sqlite"
    or not engine.url.database
    or Path(engine.url.database).resolve() != _expected_db
):
    raise RuntimeError("C1 tests require the disposable tests/journee-test.db database.")


def _login(client, username="JP Chaaya", password="test-password"):
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _populate_test_journeys(db):
    j1 = create_journey(db, "Completed Journey Alpha", date(2026, 8, 1), 1, "Test")
    j2 = create_journey(db, "Completed Journey Beta", date(2026, 8, 2), 1, "Test")
    j_draft = create_journey(db, "Draft Journey Gamma", date(2026, 8, 3), 1, "Test")
    j1.status = "completed"
    j2.status = "completed"

    r1 = Recruit(journey_id=j1.id, name="Recruit One", present=True)
    r2 = Recruit(journey_id=j1.id, name="Recruit Two", present=True)
    r3 = Recruit(journey_id=j2.id, name="Recruit Three", present=True)
    r_draft = Recruit(journey_id=j_draft.id, name="Recruit Draft", present=True)
    db.add_all([r1, r2, r3, r_draft])
    db.flush()

    e1 = Evaluator(
        journey_id=j1.id, name="Evaluator 1", role="dossard", active=True, present=True
    )
    e2 = Evaluator(
        journey_id=j2.id, name="Evaluator 2", role="dossard", active=True, present=True
    )
    db.add_all([e1, e2])
    db.flush()

    round1 = AssignmentRound(
        journey_id=j1.id, activity_code="escape_room", version=1,
        status="published", seed="esc1", created_by="Test",
    )
    round2 = AssignmentRound(
        journey_id=j2.id, activity_code="escape_room", version=1,
        status="published", seed="esc2", created_by="Test",
    )
    db.add_all([round1, round2])
    db.flush()

    # Essential: result_snapshot follows ActivityState.assignment_round_id.
    # Without these links the evaluations below do not contribute to results.
    for journey, round_record in ((j1, round1), (j2, round2)):
        state = db.scalar(select(ActivityState).where(
            ActivityState.journey_id == journey.id,
            ActivityState.code == "escape_room",
        ))
        assert state is not None
        state.assignment_round_id = round_record.id
        state.status = "closed"
    db.flush()

    a1 = Assignment(round_id=round1.id, evaluator_id=e1.id, recruit_id=r1.id, slot=1)
    a2 = Assignment(round_id=round1.id, evaluator_id=e1.id, recruit_id=r2.id, slot=2)
    a3 = Assignment(round_id=round2.id, evaluator_id=e2.id, recruit_id=r3.id, slot=1)
    db.add_all([a1, a2, a3])
    db.flush()

    resp1 = {criterion.key: 4 for criterion in RUBRICS["escape_room"].criteria}
    resp2 = {criterion.key: 2 for criterion in RUBRICS["escape_room"].criteria}
    resp3 = {criterion.key: 5 for criterion in RUBRICS["escape_room"].criteria}
    s1 = EvaluationSubmission(
        assignment_id=a1.id, journey_id=j1.id, activity_code="escape_room",
        evaluator_id=e1.id, recruit_id=r1.id, status="locked", version=2,
        score=Decimal("4.0"), responses_json=dumps(resp1),
        comments="Good communication",
    )
    s2 = EvaluationSubmission(
        assignment_id=a2.id, journey_id=j1.id, activity_code="escape_room",
        evaluator_id=e1.id, recruit_id=r2.id, status="locked",
        score=Decimal("2.0"), responses_json=dumps(resp2),
        comments="Needs improvement",
    )
    s3 = EvaluationSubmission(
        assignment_id=a3.id, journey_id=j2.id, activity_code="escape_room",
        evaluator_id=e2.id, recruit_id=r3.id, status="locked",
        score=Decimal("5.0"), responses_json=dumps(resp3), comments="Excellent",
    )
    db.add_all([s1, s2, s3])
    db.flush()

    # Nonempty version history makes profile-history preservation testable.
    for version, score, responses in (
        (1, Decimal("3.0"), {key: 3 for key in resp1}),
        (2, Decimal("4.0"), resp1),
    ):
        db.add(SubmissionVersion(
            submission_id=s1.id, version=version, score=score,
            payload_json=dumps({
                "responses": responses, "raw": {}, "score": float(score),
                "status": "locked", "comments": "Synthetic version history",
            }),
            actor_type="evaluator", actor_name=e1.name, reason="Synthetic test record",
        ))
    db.add(GeneralAssessment(
        recruit_id=r1.id, punctuality=Decimal("1.0"), respect=Decimal("1.0"),
        seriousness=Decimal("1.0"), comment="Solid candidate",
    ))
    db.add(AuditEvent(
        system_id=j1.system_id, journey_id=j1.id, actor_type="results",
        actor_name="Synthetic Reviewer", action="recruit.profile_updated",
        entity_type="recruit", entity_id=r1.id,
        before_json=dumps({"comment": ""}),
        after_json=dumps({"comment": "Solid candidate"}),
        reason="Synthetic profile history",
    ))
    db.commit()
    return j1, j2, j_draft, r1, r2, r3, r_draft


def _assert_real_evaluations_are_scored(results):
    rows = {row["name"]: row for row in results["rows"]}
    assert set(rows) == {"Recruit One", "Recruit Two", "Recruit Three"}
    for name, score, rank in (
        ("Recruit One", 4.0, 2),
        ("Recruit Two", 2.0, 3),
        ("Recruit Three", 5.0, 1),
    ):
        activity = rows[name]["activities"]["escape_room"]
        assert activity["score"] == score
        assert activity["expected"] == 1
        assert activity["submitted"] == 1
        assert activity["complete"] is True
        assert activity["rank"] == rank
    assert results["activityAverages"]["escape_room"] == pytest.approx(11 / 3)
    assert any(value > 0 for value in results["dimensionAverages"].values())


def test_completed_results_matches_completed_view_results():
    with SessionLocal() as db:
        _populate_test_journeys(db)
    # Independent ORM sessions avoid warming one implementation with the other.
    with SessionLocal() as db:
        old = _completed_view(db)["results"]
    with SessionLocal() as db:
        new = _completed_results(db)
    _assert_real_evaluations_are_scored(old)
    _assert_real_evaluations_are_scored(new)
    assert new == old


def test_completed_results_empty_scope():
    with SessionLocal() as db:
        new = _completed_results(db)
        assert new["rows"] == []
        assert new == _completed_view(db)["results"]


def test_profile_view_completed_scope_does_not_call_completed_view(client, monkeypatch):
    _login(client)
    with SessionLocal() as db:
        j1, _, _, r1, *_ = _populate_test_journeys(db)
        url = f"/api/view/journeys/{j1.id}/recruits/{r1.id}/profile?scope=completed"
        profile_key = f"{j1.id}:{r1.id}"

    # Reference request deliberately restores the old aggregation path.
    # Compare the ENTIRE profile, not just the presence of a few keys.
    with monkeypatch.context() as old_path:
        old_path.setattr(
            "app.routes_viewer._completed_results",
            lambda db, **kwargs: _completed_view(db)["results"],
        )
        reference = client.get(url)
        assert reference.status_code == 200, reference.text

    def forbidden_completed_view(db):
        raise AssertionError("_completed_view should NOT be called for a profile")

    monkeypatch.setattr("app.routes_viewer._completed_view", forbidden_completed_view)
    response = client.get(url)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data == reference.json()
    assert data["result"]["profileKey"] == profile_key
    assert data["result"]["activities"]["escape_room"]["score"] == 4.0
    entries = data["evaluations"]["escape_room"]
    assert len(entries) == 1
    submission = entries[0]["submission"]
    assert submission["score"] == 4.0
    assert submission["comments"] == "Good communication"
    assert [item["version"] for item in submission["history"]] == [2, 1]
    assert [item["score"] for item in submission["history"]] == [4.0, 3.0]
    assert any(item["reason"] == "Synthetic profile history" for item in data["history"])

    # The full endpoint must KEEP its existing full roster/results path.
    with pytest.raises(AssertionError, match="_completed_view should NOT be called"):
        client.get("/api/view/completed")


def test_profile_view_completed_scope_edge_cases(client):
    _login(client)
    with SessionLocal() as db:
        j1, _, j_draft, r1, r2, _, r_draft = _populate_test_journeys(db)
        completed_id, draft_id = j1.id, j_draft.id
        present_id, absent_id, draft_recruit_id = r1.id, r2.id, r_draft.id
        r2.present = False
        db.commit()

    response = client.get(
        f"/api/view/journeys/{draft_id}/recruits/{draft_recruit_id}/profile?scope=completed"
    )
    assert response.status_code == 404
    assert "not part of a completed Journee" in response.json()["detail"]
    assert client.get(
        f"/api/view/journeys/{completed_id}/recruits/nonexistent-id/profile?scope=completed"
    ).status_code == 404
    assert client.get(
        f"/api/view/journeys/{completed_id}/recruits/{draft_recruit_id}/profile?scope=completed"
    ).status_code == 404
    # Absent recruits keep the preexisting unranked profile fallback.
    absent = client.get(
        f"/api/view/journeys/{completed_id}/recruits/{absent_id}/profile?scope=completed"
    )
    assert absent.status_code == 200, absent.text
    assert absent.json()["result"]["overallRank"] is None
    assert client.get(
        f"/api/view/journeys/{completed_id}/recruits/{present_id}/profile?scope=completed"
    ).status_code == 200


def test_select_query_count_reduction():
    with SessionLocal() as db:
        _populate_test_journeys(db)

    def measured(call):
        queries = []

        def count_select(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                queries.append(statement)

        event.listen(engine, "before_cursor_execute", count_select)
        try:
            with SessionLocal() as db:
                payload = call(db)
        finally:
            event.remove(engine, "before_cursor_execute", count_select)
        return payload, queries

    old, old_queries = measured(lambda db: _completed_view(db)["results"])
    new, new_queries = measured(_completed_results)
    assert old == new
    _assert_real_evaluations_are_scored(new)
    assert any("evaluation_submissions" in sql.lower() for sql in new_queries)
    difference = len(old_queries) - len(new_queries)
    print("\nTwo completed Journees with linked evaluator submissions; helper calls only:")
    print(f"  _completed_view:    {len(old_queries)} SELECT queries")
    print(f"  _completed_results: {len(new_queries)} SELECT queries")
    print(f"  Difference:         {difference} fewer SELECT queries")
    # Specific regression bound for this two-Journee fixture and C1 baseline.
    # Site-wide roster count batching also improves the full-view reference.
    # Historical C1 alone was39 ->15; site-wide roster batching was31 ->15.
    # Residual full-view discarded metadata removal reduces this to25 ->15.
    assert difference == 10


def test_completed_results_and_profile_respect_workspace_isolation(client):
    _login(client)
    with SessionLocal() as db:
        j1, _, _, *_ = _populate_test_journeys(db)
        first_system_id = j1.system_id
        first_system = db.get(AssessmentSystem, first_system_id)
        assert first_system is not None
        record = db.scalar(select(AssessmentSystemVersion).where(
            AssessmentSystemVersion.system_id == first_system_id,
            AssessmentSystemVersion.version == first_system.published_version,
        ))
        definition_json = record.definition_json if record else first_system.draft_json
        other_system = AssessmentSystem(
            name="Other synthetic workspace", slug="c1-other-test-workspace",
            draft_json=definition_json, published_version=1, updated_by="Test",
        )
        db.add(other_system)
        db.flush()
        other_system_id = other_system.id
        db.add(AssessmentSystemVersion(
            system_id=other_system_id, version=1, definition_json=definition_json,
            published_by="Test", change_summary="Synthetic workspace isolation test",
        ))
        token = select_system(other_system_id)
        try:
            other_journey = create_journey(
                db, "Other workspace completed Journee", date(2026, 8, 4), 1, "Test"
            )
            other_journey.status = "completed"
            other_recruit = Recruit(
                journey_id=other_journey.id, name="Other Workspace Recruit", present=True
            )
            db.add(other_recruit)
            db.commit()
            other_journey_id, other_recruit_id = other_journey.id, other_recruit.id
        finally:
            reset_system(token)

    for system_id, expected_names in (
        (first_system_id, {"Recruit One", "Recruit Two", "Recruit Three"}),
        (other_system_id, {"Other Workspace Recruit"}),
    ):
        token = select_system(system_id)
        try:
            with SessionLocal() as db:
                new = _completed_results(db)
                old = _completed_view(db)["results"]
                assert new == old
                assert {row["name"] for row in new["rows"]} == expected_names
        finally:
            reset_system(token)
    # The logged-in account remains in the first workspace.
    response = client.get(
        f"/api/view/journeys/{other_journey_id}/recruits/{other_recruit_id}/profile?scope=completed"
    )
    assert response.status_code == 404, response.text
    bootstrap = client.get("/api/view/bootstrap")
    assert bootstrap.status_code == 200
    assert other_journey_id not in {row["id"] for row in bootstrap.json()["journeys"]}
    assert {row["name"] for row in bootstrap.json()["completed"]["results"]["rows"]} == {
        "Recruit One", "Recruit Two", "Recruit Three",
    }


def test_completed_profile_reflects_general_assessment_save(client):
    session = _login(client)
    with SessionLocal() as db:
        j1, _, _, r1, *_ = _populate_test_journeys(db)
        url = f"/api/view/journeys/{j1.id}/recruits/{r1.id}/profile"
    before_response = client.get(url + "?scope=completed")
    assert before_response.status_code == 200, before_response.text
    before = before_response.json()
    saved = client.put(url, headers={"X-CSRF-Token": session["csrfToken"]}, json={
        "values": {"punctuality": 0.0, "respect": 0.0, "seriousness": 0.0},
        "comment": "Synthetic saved correction", "notes": "Local test only",
        "base_version": before["assessment"]["version"],
    })
    assert saved.status_code == 200, saved.text
    after_response = client.get(url + "?scope=completed")
    assert after_response.status_code == 200, after_response.text
    after = after_response.json()
    assert after["assessment"]["comment"] == "Synthetic saved correction"
    assert after["result"]["generalAverage"] == 0.0
    assert after["result"]["overallScore"] < before["result"]["overallScore"]
    assert after["evaluations"] == before["evaluations"]
    with SessionLocal() as db:
        reference = _completed_view(db)["results"]
    expected_row = next(
        row for row in reference["rows"]
        if row["profileKey"] == after["result"]["profileKey"]
    )
    assert after["result"] == expected_row
    assert after["dimensionAverages"] == reference["dimensionAverages"]
    assert after["activityAverages"] == reference["activityAverages"]


def test_single_journee_profile_uses_results_only_for_overall_rank(client, monkeypatch):
    _login(client)
    with SessionLocal() as db:
        j1, _, _, r1, *_ = _populate_test_journeys(db)
        url = f"/api/view/journeys/{j1.id}/recruits/{r1.id}/profile"

    def forbidden(db):
        raise AssertionError("Single-Journee profiles must not load completed rosters")

    monkeypatch.setattr("app.routes_viewer._completed_view", forbidden)
    response = client.get(url)
    assert response.status_code == 200, response.text
    assert response.json()["result"]["activities"]["escape_room"]["score"] == 4.0


def test_profile_still_requires_login(client):
    with SessionLocal() as db:
        j1, _, _, r1, *_ = _populate_test_journeys(db)
        url = f"/api/view/journeys/{j1.id}/recruits/{r1.id}/profile?scope=completed"
    response = client.get(url)
    assert response.status_code == 401, response.text
