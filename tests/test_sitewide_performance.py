"""Fictional linked fixture: full HTTP query/checkout baseline, never production."""
import time
from sqlalchemy import event
from app.db import engine, SessionLocal
from app.models import Journey, RoomPlan, RoomPlanRecruit, RoomPlanEvaluator, Recruit, Evaluator
from sqlalchemy import select
from test_profile_performance_c2 import _fixture
from test_viewer_performance_c1 import _login


def measure(client, paths):
    counts = {"sql": 0, "checkouts": 0}
    def sql(*args): counts["sql"] += 1
    def checkout(*args): counts["checkouts"] += 1
    event.listen(engine, "before_cursor_execute", sql)
    event.listen(engine, "checkout", checkout)
    started = time.perf_counter()
    try:
        responses = [client.get(path) for path in paths]
        assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
        return [r.json() for r in responses], {**counts, "requests": len(paths), "ms": round((time.perf_counter()-started)*1000, 1)}
    finally:
        event.remove(engine, "before_cursor_execute", sql)
        event.remove(engine, "checkout", checkout)


def seed(client):
    _login(client)
    ids = _fixture()
    with SessionLocal() as db:
        j = ids["journey_id"]
        db.get(Journey, j).room_count = 2
        recruits = list(db.scalars(select(Recruit).where(Recruit.journey_id == j)))
        evaluators = list(db.scalars(select(Evaluator).where(Evaluator.journey_id == j)))
        for code in ("escape_room", "sport"):
            plan = RoomPlan(journey_id=j, activity_code=code, version=1 if code == "escape_room" else 2, status="published", seed="fiction", created_by="Test")
            db.add(plan); db.flush()
            db.add_all([RoomPlanRecruit(plan_id=plan.id, recruit_id=r.id, room_number=index % 2 + 1) for index, r in enumerate(recruits)])
            db.add_all([RoomPlanEvaluator(plan_id=plan.id, evaluator_id=e.id, room_number=index % 2 + 1) for index, e in enumerate(evaluators)])
        db.commit()
    return j


def test_http_flow_measurement(client):
    j = seed(client)
    base = f"/api/admin/journeys/{j}"
    old_open, opening = measure(client, [base, base+"/dashboard"])
    new_open, bundled_open = measure(client, [base+"/dashboard?include_details=true"])
    assert new_open[0]["journey"] == old_open[0]
    for key in old_open[1]:
        if key != "journey": assert new_open[0][key] == old_open[1][key]
    paths = [base+"/activities/escape_room/operation", base+"/activities/escape_room/rooms?status=published", base+"/activities/escape_room/rooms?status=preview", base+"/assignments/escape_room?status=published", base+"/assignments/escape_room?status=preview"]
    old, loading = measure(client, paths)
    new, bundled = measure(client, [base+"/activities/escape_room/workspace"])
    assert list(new[0].values()) == old
    assert bundled["sql"] < loading["sql"]
    assert bundled["checkouts"] < loading["checkouts"]
    print("HTTP_BASELINE", {"open": opening, "activity": loading, "bundled_open": bundled_open, "bundled_activity": bundled})


def test_bundle_initialization_and_authorization(client):
    j = seed(client)
    base = f"/api/admin/journeys/{j}/activities"
    assert client.get(base+"/unknown/workspace").status_code == 404
    assert client.get("/api/admin/journeys/missing/activities/sport/workspace").status_code == 404
    first = client.get(base+"/sport/workspace")
    assert first.status_code == 200
    assert first.json() == client.get(base+"/sport/workspace").json()
    client.cookies.clear()
    assert client.get(base+"/sport/workspace").status_code == 401


def test_bundle_tenant_isolation_and_revocation(client):
    from datetime import date
    from app.models import AssessmentSystem, AssessmentSystemVersion, UserSession
    from app.services import create_journey
    from app.tenant import select_system, reset_system
    j = seed(client)
    with SessionLocal() as db:
        first = db.scalar(select(AssessmentSystem))
        record = db.scalar(select(AssessmentSystemVersion).where(AssessmentSystemVersion.system_id == first.id))
        other = AssessmentSystem(name="Fictional Other", slug="fictional-other", draft_json=record.definition_json, published_version=1, updated_by="Test")
        db.add(other);db.flush()
        db.add(AssessmentSystemVersion(system_id=other.id, version=1, definition_json=record.definition_json, published_by="Test"))
        token = select_system(other.id)
        try:
            other_j = create_journey(db,"Other tenant fixture",date(2026,8,4),2,"Test")
            db.commit();other_id=other_j.id
        finally: reset_system(token)
    assert client.get(f"/api/admin/journeys/{other_id}/activities/escape_room/workspace").status_code == 404
    assert client.get(f"/api/admin/journeys/{j}/activities/escape_room/workspace").status_code == 200
    with SessionLocal() as db:
        for session in db.scalars(select(UserSession)): db.delete(session)
        db.commit()
    assert client.get(f"/api/admin/journeys/{j}/activities/escape_room/workspace").status_code == 401
