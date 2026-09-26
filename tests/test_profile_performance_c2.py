"""C2: batch profile submissions/history, using fictional local data only.

Requires the committed C1 test module and existing repository conftest.py.
Does not change production configuration, dependencies, or live databases.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import event, select

from app.db import SessionLocal, engine
from app.models import (
    ActivityState, AdminEvaluation, Assignment, AssignmentRound,
    EvaluationSubmission, Evaluator, Recruit, SubmissionVersion,
)
from app.routes_admin import _profile_evaluation_details, _submission_payload
from app.rubric import ACTIVITY_ORDER, RUBRICS
from app.utils import dumps
from test_viewer_performance_c1 import _login, _populate_test_journeys


_expected_db = Path(__file__).with_name("journee-test.db").resolve()
if (
    engine.url.get_backend_name() != "sqlite"
    or not engine.url.database
    or Path(engine.url.database).resolve() != _expected_db
):
    raise RuntimeError("C2 tests require the disposable tests/journee-test.db database.")


def _legacy_details(db, recruit_id, states, evaluators):
    details: dict[str, list[dict]] = {code: [] for code in ACTIVITY_ORDER}
    for state in states:
        if not state.assignment_round_id:
            continue
        assignments = list(
            db.scalars(
                select(Assignment).where(
                    Assignment.round_id == state.assignment_round_id,
                    Assignment.recruit_id == recruit_id,
                )
            )
        )
        for assignment in assignments:
            submission = db.scalar(
                select(EvaluationSubmission).where(EvaluationSubmission.assignment_id == assignment.id)
            )
            evaluator = evaluators.get(assignment.evaluator_id)
            details[state.code].append(
                {
                    "assignmentId": assignment.id,
                    "slot": assignment.slot,
                    "roomNumber": assignment.room_number,
                    "evaluatorId": assignment.evaluator_id,
                    "evaluatorName": evaluator.name if evaluator else "Unknown",
                    "evaluatorRole": evaluator.role if evaluator else "",
                    "submission": _submission_payload(db, submission, evaluator.name if evaluator else "Unknown")
                    if submission
                    else None,
                }
            )
    return details


def _fixture():
    """Two linked activities, multiple evaluators, missing/draft/history/admin cases."""
    with SessionLocal() as db:
        j1, _, _, r1, r2, *_ = _populate_test_journeys(db)
        state = db.scalar(select(ActivityState).where(
            ActivityState.journey_id == j1.id, ActivityState.code == "escape_room",
        ))
        assert state is not None and state.assignment_round_id
        first = db.scalar(select(EvaluationSubmission).where(
            EvaluationSubmission.recruit_id == r1.id,
            EvaluationSubmission.activity_code == "escape_room",
        ))
        second_evaluator = Evaluator(
            journey_id=j1.id, name="C2 Second Evaluator", role="overall",
            active=True, present=True,
        )
        db.add(second_evaluator)
        db.flush()
        second_assignment = Assignment(
            round_id=state.assignment_round_id, evaluator_id=second_evaluator.id,
            recruit_id=r1.id, slot=2, room_number=2,
        )
        db.add(second_assignment)
        negotiation_round = AssignmentRound(
            journey_id=j1.id, activity_code="negotiation", version=1,
            status="published", seed="c2-negotiation", created_by="Synthetic Test",
        )
        old_round = AssignmentRound(
            journey_id=j1.id, activity_code="escape_room", version=2,
            status="preview", seed="c2-noncurrent", created_by="Synthetic Test",
        )
        db.add_all([negotiation_round, old_round])
        db.flush()
        negotiation_state = db.scalar(select(ActivityState).where(
            ActivityState.journey_id == j1.id, ActivityState.code == "negotiation",
        ))
        assert negotiation_state is not None
        negotiation_state.assignment_round_id = negotiation_round.id
        negotiation_state.status = "closed"
        negotiation_assignment = Assignment(
            round_id=negotiation_round.id, evaluator_id=first.evaluator_id,
            recruit_id=r1.id, slot=1, room_number=2,
        )
        missing_assignment = Assignment(
            round_id=negotiation_round.id, evaluator_id=second_evaluator.id,
            recruit_id=r1.id, slot=2, room_number=2,
        )
        old_assignment = Assignment(
            round_id=old_round.id, evaluator_id=second_evaluator.id,
            recruit_id=r1.id, slot=1,
        )
        db.add_all([negotiation_assignment, missing_assignment, old_assignment])
        db.flush()
        draft = EvaluationSubmission(
            assignment_id=second_assignment.id, journey_id=j1.id,
            activity_code="escape_room", evaluator_id=second_evaluator.id,
            recruit_id=r1.id, status="draft", score=Decimal("2"), version=1,
            responses_json=dumps({c.key: 2 for c in RUBRICS["escape_room"].criteria}),
            raw_payload_json=dumps({"synthetic_original_input": 12}),
            comments="Draft must remain visible, not counted as submitted",
        )
        negotiation = EvaluationSubmission(
            assignment_id=negotiation_assignment.id, journey_id=j1.id,
            activity_code="negotiation", evaluator_id=first.evaluator_id,
            recruit_id=r1.id, status="submitted", score=Decimal("4"), version=3,
            responses_json=dumps({c.key: 4 for c in RUBRICS["negotiation"].criteria}),
            comments="Nonempty complete history",
        )
        old_submission = EvaluationSubmission(
            assignment_id=old_assignment.id, journey_id=j1.id,
            activity_code="escape_room", evaluator_id=second_evaluator.id,
            recruit_id=r1.id, status="locked", score=Decimal("5"), version=1,
            responses_json=dumps({c.key: 5 for c in RUBRICS["escape_room"].criteria}),
            comments="Must not leak from noncurrent round",
        )
        db.add_all([draft, negotiation, old_submission])
        db.flush()
        for version in (1, 2, 3):
            db.add(SubmissionVersion(
                submission_id=negotiation.id, version=version,
                score=Decimal(version + 1),
                payload_json=dumps({"syntheticVersion": version, "raw": {"count": version}}),
                actor_type="evaluator", actor_name="Synthetic Evaluator",
                reason=f"Synthetic reason {version}",
            ))
        db.add(SubmissionVersion(
            submission_id=old_submission.id, version=1, score=Decimal("5"),
            payload_json=dumps({"noncurrent": True}), actor_type="evaluator",
            actor_name="Synthetic Old Evaluator", reason="Must not be returned",
        ))
        db.add(AdminEvaluation(
            journey_id=j1.id, recruit_id=r1.id, activity_code="escape_room",
            score=Decimal("3"), updated_by="Synthetic Administrator",
            responses_json=dumps({c.key: 3 for c in RUBRICS["escape_room"].criteria}),
            comments="Existing admin adjustment must be preserved",
        ))
        db.commit()
        return {
            "journey_id": j1.id, "recruit_id": r1.id,
            "first_submission": first.id, "draft_id": draft.id,
            "old_submission": old_submission.id, "old_assignment": old_assignment.id,
            "missing_assignment": missing_assignment.id,
            "second_evaluator": second_evaluator.id,
        }


def _measure_details(ids, implementation):
    with SessionLocal() as db:
        # These unchanged setup queries are outside this helper-only count.
        states = list(db.scalars(select(ActivityState).where(
            ActivityState.journey_id == ids["journey_id"],
        )))
        evaluators = {row.id: row for row in db.scalars(select(Evaluator).where(
            Evaluator.journey_id == ids["journey_id"],
        ))}
        statements = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", capture)
        try:
            payload = implementation(db, ids["recruit_id"], states, evaluators)
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        return payload, statements


def test_c2_helper_matches_old_payload_and_batches_queries():
    ids = _fixture()
    old, old_sql = _measure_details(ids, _legacy_details)
    new, new_sql = _measure_details(ids, _profile_evaluation_details)
    assert new == old  # Includes assignment order, all values and history order.
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in new_sql)
    assert len(new_sql) == 4  # 2 unchanged assignment reads + 1 submission + 1 history.
    assert len(old_sql) == 9  # 2 assignment reads + 4 submission reads + 3 history reads.
    print("\nC2 evaluation-details helper only, two linked activities:")
    print(f"  original: {len(old_sql)} SELECT queries")
    print(f"  batched:  {len(new_sql)} SELECT queries")
    print(f"  saved:    {len(old_sql) - len(new_sql)} SELECT queries")
    assert len(new["escape_room"]) == 2
    assert len(new["negotiation"]) == 2
    by_id = {
        entry["submission"]["id"]: entry["submission"]
        for entries in new.values() for entry in entries if entry["submission"]
    }
    assert ids["old_submission"] not in by_id
    assert [v["version"] for v in by_id[ids["first_submission"]]["history"]] == [2, 1]
    assert by_id[ids["draft_id"]]["status"] == "draft"
    assert by_id[ids["draft_id"]]["history"] == []
    assert by_id[ids["draft_id"]]["raw"] == {"synthetic_original_input": 12}
    missing = next(e for e in new["negotiation"] if e["assignmentId"] == ids["missing_assignment"])
    assert missing["submission"] is None
    submitted = next(e["submission"] for e in new["negotiation"] if e["submission"])
    assert [v["version"] for v in submitted["history"]] == [3, 2, 1]
    assert [v["reason"] for v in submitted["history"]] == [
        "Synthetic reason 3", "Synthetic reason 2", "Synthetic reason 1",
    ]


@pytest.mark.parametrize("portal,suffix", [
    ("view", ""), ("view", "?scope=completed"), ("admin", ""),
])
def test_c2_whole_profile_matches_old_path(client, monkeypatch, portal, suffix):
    _login(client)
    ids = _fixture()
    url = f"/api/{portal}/journeys/{ids['journey_id']}/recruits/{ids['recruit_id']}/profile{suffix}"
    with monkeypatch.context() as old_path:
        old_path.setattr("app.routes_admin._profile_evaluation_details", _legacy_details)
        reference = client.get(url)
    actual = client.get(url)
    assert reference.status_code == actual.status_code == 200, actual.text
    assert actual.json() == reference.json()
    payload = actual.json()
    assert payload["evaluations"]["escape_room"][0]["isAdmin"] is True
    assert payload["adminEvaluations"]["escape_room"]["comments"] == "Existing admin adjustment must be preserved"
    assert any(h["reason"] == "Synthetic profile history" for h in payload["history"])


def test_c2_empty_prefetched_history_does_not_trigger_a_query():
    ids = _fixture()
    with SessionLocal() as db:
        submission = db.get(EvaluationSubmission, ids["first_submission"])
        def forbidden_query(*args, **kwargs):
            raise AssertionError("Prefetched empty history must not query the database")
        event.listen(engine, "before_cursor_execute", forbidden_query)
        try:
            payload = _submission_payload(db, submission, "Synthetic", versions=[])
        finally:
            event.remove(engine, "before_cursor_execute", forbidden_query)
        assert payload["history"] == []
        # Standalone callers must still get the full history by default.
        normal = _submission_payload(db, submission, "Synthetic")
        assert [v["version"] for v in normal["history"]] == [2, 1]


def test_c2_prefetched_history_matches_default_serializer():
    ids = _fixture()
    with SessionLocal() as db:
        submission = db.get(EvaluationSubmission, ids["first_submission"])
        versions = list(db.scalars(select(SubmissionVersion).where(
            SubmissionVersion.submission_id == submission.id,
        ).order_by(SubmissionVersion.version.desc())))
        normal = _submission_payload(db, submission, "Synthetic")
        assert _submission_payload(db, submission, "Synthetic", versions=versions) == normal


def test_c2_no_active_rounds_returns_empty_without_querying():
    with SessionLocal() as db:
        def forbidden_query(*args, **kwargs):
            raise AssertionError("No selected rounds: no queries should be issued")
        event.listen(engine, "before_cursor_execute", forbidden_query)
        try:
            result = _profile_evaluation_details(db, "no-recruit", [], {})
        finally:
            event.remove(engine, "before_cursor_execute", forbidden_query)
        assert result == {code: [] for code in ACTIVITY_ORDER}


def test_c2_assignments_without_submissions_skip_history_query():
    ids = _fixture()
    with SessionLocal() as db:
        recruit = Recruit(journey_id=ids["journey_id"], name="C2 Missing Only", present=True)
        db.add(recruit)
        db.flush()
        state = db.scalar(select(ActivityState).where(
            ActivityState.journey_id == ids["journey_id"], ActivityState.code == "escape_room",
        ))
        db.add(Assignment(
            round_id=state.assignment_round_id, recruit_id=recruit.id,
            evaluator_id=ids["second_evaluator"], slot=1,
        ))
        db.commit()
        ids["recruit_id"] = recruit.id
    result, statements = _measure_details(ids, _profile_evaluation_details)
    assert len(result["escape_room"]) == 1
    assert result["escape_room"][0]["submission"] is None
    assert all("submission_versions" not in sql.lower() for sql in statements)


def test_c2_unknown_evaluator_fallback_is_unchanged():
    ids = _fixture()
    def no_evaluators(db, recruit_id, states, _evaluators):
        return _profile_evaluation_details(db, recruit_id, states, {})
    result, _ = _measure_details(ids, no_evaluators)
    for entries in result.values():
        for entry in entries:
            assert entry["evaluatorName"] == "Unknown"
            assert entry["evaluatorRole"] == ""
            if entry["submission"]:
                assert entry["submission"]["evaluatorName"] == "Unknown"


def test_c2_next_request_sees_new_submission_history(client):
    _login(client)
    ids = _fixture()
    url = f"/api/view/journeys/{ids['journey_id']}/recruits/{ids['recruit_id']}/profile?scope=completed"
    before = client.get(url)
    assert before.status_code == 200, before.text
    with SessionLocal() as db:
        submission = db.get(EvaluationSubmission, ids["first_submission"])
        submission.version = 3
        submission.comments = "New synthetic correction"
        db.add(SubmissionVersion(
            submission_id=submission.id, version=3, score=submission.score,
            payload_json=dumps({"comments": submission.comments}),
            actor_type="admin", actor_name="Synthetic Administrator",
            reason="C2 freshness check",
        ))
        db.commit()
    after = client.get(url)
    assert after.status_code == 200, after.text
    entry = next(e["submission"] for e in after.json()["evaluations"]["escape_room"]
                 if e["submission"] and e["submission"]["id"] == ids["first_submission"])
    assert entry["comments"] == "New synthetic correction"
    assert [v["version"] for v in entry["history"]] == [3, 2, 1]
    assert entry["history"][0]["reason"] == "C2 freshness check"
