from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

from app import main
from app.db import engine


@pytest.fixture(autouse=True)
def reset_fixture_revision(clean_database):
    # The common fixture recreates ORM tables; Alembic's table is not ORM metadata.
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    main.database_startup_error = None
    yield
    main.database_startup_error = None


def enable_verified(monkeypatch):
    monkeypatch.setattr(main, "settings", replace(main.settings, startup_verify_only=True))
    def forbidden(*args, **kwargs):
        raise AssertionError("startup attempted initialization")
    for name in ("initialize_database", "ensure_owner_account", "ensure_platform_owner", "ensure_assessment_system"):
        monkeypatch.setattr(main, name, forbidden)


def test_verified_startup_is_read_only_and_normal_operations_remain_enabled(client, monkeypatch):
    enable_verified(monkeypatch)
    statements = []
    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip().lower())
    event.listen(engine, "before_cursor_execute", record)
    try:
        with TestClient(main.app) as verified:
            assert verified.get("/health/ready").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert statements
    assert all(s.startswith(("select", "pragma")) for s in statements)
    with TestClient(main.app) as verified:
        response = verified.post("/api/auth/login", json={"username": "JP Chaaya", "password": "test-password"})
        assert response.status_code == 200, response.text
        assert verified.get("/api/auth/session").status_code == 200


@pytest.mark.parametrize("damage", ["revision", "configuration", "owner", "tables", "workspace"])
def test_missing_prerequisites_fail_and_readiness_does_not_clear_failure(client, monkeypatch, damage):
    sql = {
        "revision": "update alembic_version set version_num='wrong'",
        "configuration": "update assessment_system_versions set definition_json='{}'",
        "owner": "update user_accounts set platform_account_id=null",
        "tables": "drop table admin_evaluations",
        "workspace": "update assessment_systems set status='archived'",
    }[damage]
    with engine.begin() as connection:
        connection.execute(text(sql))
    enable_verified(monkeypatch)
    with TestClient(main.app) as verified:
        assert main.database_startup_error
        for _ in range(2):
            response = verified.get("/health/ready")
            assert response.status_code == 503
            assert "prerequisites" in response.json()["detail"]
        assert verified.get("/api/admin/journeys").status_code == 503
    main.database_startup_error = None


def test_verify_only_is_opt_in(monkeypatch):
    from app.config import load_settings
    monkeypatch.delenv("LRC_STARTUP_VERIFY_ONLY", raising=False)
    assert not load_settings().startup_verify_only
    monkeypatch.setenv("LRC_STARTUP_VERIFY_ONLY", "true")
    assert load_settings().startup_verify_only


def test_postgres_transaction_is_read_only_before_inspecting_schema(monkeypatch):
    from contextlib import contextmanager
    calls = []
    class Result:
        def scalar_one(self):
            return "wrong"
    class Connection:
        dialect = type("Dialect", (), {"name": "postgresql"})()
        def exec_driver_sql(self, sql):
            calls.append(sql)
            return Result()
    class Database:
        def connection(self):
            return Connection()
    @contextmanager
    def session():
        yield Database()
    monkeypatch.setattr(main, "SessionLocal", session)
    with pytest.raises(RuntimeError, match="revision"):
        main.verify_existing_startup()
    assert calls[0] == "SET TRANSACTION READ ONLY"
    assert calls[1].startswith("select version_num")


def test_second_workspace_is_also_verified(client, monkeypatch):
    from test_workspace_recovery import create_workspaces
    create_workspaces(client)
    with engine.begin() as connection:
        connection.execute(text("update assessment_system_versions set definition_json='{}' where system_id=(select id from assessment_systems where name='Test workspace')"))
    enable_verified(monkeypatch)
    with TestClient(main.app) as verified:
        assert verified.get("/health/ready").status_code == 503
    main.database_startup_error = None
