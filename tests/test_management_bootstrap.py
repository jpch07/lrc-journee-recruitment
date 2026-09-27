"""Full-payload parity and bounded fictional Management measurements."""
from sqlalchemy import event, select
from app.db import SessionLocal, engine
from app.models import Journey, UserSession, UserAccount
from app.routes_viewer import _single_journey_view, _aggregate_results
from app.services import serialize_journey
from app.rubric import ACTIVITY_ORDER, RUBRICS
from test_viewer_performance_c1 import _login, _populate_test_journeys
from test_sitewide_performance import measure


def reference_completed(db):
    # Exact previous full-view construction, including discarded metadata reads.
    from sqlalchemy import func
    items = list(db.scalars(select(Journey).where(Journey.status == "completed").order_by(
        Journey.event_date.desc(), func.lower(Journey.name))))
    payloads = [_single_journey_view(db, item) for item in items]
    return {"scope": "completed", "journey": None,
            "journeys": [serialize_journey(db, item) for item in items],
            "activities": [{"code": code, "name": RUBRICS[code].name} for code in ACTIVITY_ORDER],
            "recruits": [row for payload in payloads for row in payload["recruits"]],
            "evaluators": [row for payload in payloads for row in payload["evaluators"]],
            "results": _aggregate_results([(item, payload["results"]) for item, payload in zip(items, payloads)])}


def test_bootstrap_parity_and_queries(client, monkeypatch):
    _login(client)
    with SessionLocal() as db:
        _populate_test_journeys(db)
    with monkeypatch.context() as old:
        old.setattr("app.routes_viewer._completed_view", reference_completed)
        expected, before = measure(client, ["/api/auth/session", "/api/view/journeys", "/api/view/completed"])
    actual, after = measure(client, ["/api/view/bootstrap"])
    assert actual[0] == {"session": expected[0], "journeys": expected[1], "completed": expected[2]}
    assert client.get("/api/view/completed").json() == expected[2]
    assert after["sql"] < before["sql"]
    assert after["checkouts"] < before["checkouts"]
    print("MANAGEMENT_HTTP", {"before": before, "after": after})
    # No startup, audit, roster initialization or data writes in the new GET.
    writes = []
    def record(_conn, _cursor, statement, *_args):
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER"}:
            writes.append(statement.split()[0])
    event.listen(engine, "before_cursor_execute", record)
    try:
        assert client.get("/api/view/bootstrap").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert not writes


def test_bootstrap_empty_and_revoked_access(client):
    _login(client)
    assert client.get("/api/view/bootstrap").json()["completed"]["results"]["rows"] == []
    with SessionLocal() as db:
        account = db.scalar(select(UserAccount))
        account.active = False
        db.commit()
    assert client.get("/api/view/bootstrap").status_code == 401


def test_bootstrap_freshness_and_session_revocation(client):
    _login(client)
    with SessionLocal() as db:
        j1, *_ = _populate_test_journeys(db)
        journey_id = j1.id
    assert any(row["id"] == journey_id for row in client.get("/api/view/bootstrap").json()["completed"]["journeys"])
    with SessionLocal() as db:
        db.get(Journey, journey_id).status = "draft"
        db.commit()
    changed = client.get("/api/view/bootstrap").json()
    assert not any(row["id"] == journey_id for row in changed["completed"]["journeys"])
    assert any(row["id"] == journey_id for row in changed["journeys"])
    with SessionLocal() as db:
        for session in db.scalars(select(UserSession)):
            db.delete(session)
        db.commit()
    assert client.get("/api/view/bootstrap").status_code == 401
