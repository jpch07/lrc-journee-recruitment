from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import event

from app import main
from app.auth import USER_COOKIE, _token_hash
from app.assessment_config import blank_assessment_definition
from app.db import SessionLocal, engine
from app.models import AssessmentSystem, AssessmentSystemVersion, UserAccount, UserSession
from app.utils import dumps


def seed_system():
    definition = blank_assessment_definition()
    definition.name = "First published configuration"
    raw = dumps(definition.model_dump(mode="json"))
    with SessionLocal() as db:
        system = AssessmentSystem(name=definition.name, slug="runtime-lookup", draft_json=raw, published_version=1)
        db.add(system)
        db.flush()
        db.add(AssessmentSystemVersion(system_id=system.id, version=1, definition_json=raw, published_by="Test"))
        db.commit()
        return system.id, definition


@contextmanager
def select_statements():
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def test_session_workspace_lookup_uses_one_join_and_observes_session_revocation():
    system_id, _ = seed_system()
    with SessionLocal() as db:
        account = UserAccount(system_id=system_id, username="Query test", password_hash="unused")
        db.add(account)
        db.flush()
        db.add(UserSession(token_hash=_token_hash("test-token"), account_id=account.id,
                           csrf_token="test-csrf", expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
        db.commit()
    request = Request({"type": "http", "method": "GET", "path": "/api/evaluator/home", "query_string": b"",
                       "headers": [(b"cookie", f"{USER_COOKIE}=test-token".encode())], "server": ("testserver", 80)})
    with select_statements() as statements:
        assert main._request_system_id(request) == system_id
    assert len(statements) == 1
    assert "JOIN user_sessions" in statements[0]
    # There is deliberately no identity cache. If the session disappears, the
    # join no longer provides a system and normal no-cookie resolution resumes.
    with SessionLocal() as db:
        db.delete(db.get(UserSession, _token_hash("test-token")))
        # Disabling the lone workspace also prevents the compatibility fallback.
        db.get(AssessmentSystem, system_id).status = "archived"
        db.commit()
    assert main._request_system_id(request) is None


def test_runtime_lookup_uses_one_narrow_join_and_cache_returns_deep_copies(monkeypatch):
    system_id, definition = seed_system()
    main._parsed_runtime_definition.cache_clear()
    parses = []
    original = main.load_stored_definition

    def parse(value):
        parses.append(True)
        return original(value)

    monkeypatch.setattr(main, "load_stored_definition", parse)
    with select_statements() as statements:
        first = main._runtime_for_system(system_id)
    assert len(statements) == 1
    assert "JOIN assessment_system_versions" in statements[0]
    assert "draft_json" not in statements[0]
    first.name = "Changed in this request"
    first.activities[0].name = "Changed nested field"
    with select_statements() as statements:
        second = main._runtime_for_system(system_id)
    assert len(statements) == 1  # Publication is checked, even on a parse-cache hit.
    assert len(parses) == 1
    assert second.name == definition.name
    assert second.activities[0].name == definition.activities[0].name
    assert main._parsed_runtime_definition.cache_info().maxsize == 32


def test_publishing_and_in_place_repairs_are_immediately_visible_without_ttl():
    system_id, definition = seed_system()
    assert main._runtime_for_system(system_id).name == definition.name
    with SessionLocal() as db:
        system = db.get(AssessmentSystem, system_id)
        system.published_version = 2
        definition.name = "Published version two"
        row = AssessmentSystemVersion(system_id=system_id, version=2,
                                      definition_json=dumps(definition.model_dump(mode="json")), published_by="Test")
        db.add(row)
        db.commit()
        version_id = row.id
    assert main._runtime_for_system(system_id).name == "Published version two"
    with SessionLocal() as db:
        definition.name = "Repaired version two"
        db.get(AssessmentSystemVersion, version_id).definition_json = dumps(definition.model_dump(mode="json"))
        db.commit()
    assert main._runtime_for_system(system_id).name == "Repaired version two"
