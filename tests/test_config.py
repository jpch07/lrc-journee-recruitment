from __future__ import annotations

import pytest

from app.config import Settings, load_settings


def production_settings(**overrides) -> Settings:
    values = {
        "database_url": "postgresql+psycopg://user:secret@example.invalid/lrc",
        "admin_password_hash": "$argon2id$v=19$m=65536,t=3,p=4$example$example",
        "admin_password": "",
        "session_secret": "a-unique-production-session-secret-with-32-chars",
        "cookie_secure": True,
        "session_hours": 12,
        "environment": "production",
        "test_tools_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)


def test_valid_production_settings_pass() -> None:
    production_settings().validate_production()


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"database_url": "sqlite:///data/journee.db"}, "external PostgreSQL"),
        ({"admin_password_hash": ""}, "Argon2 hash"),
        ({"session_secret": "short"}, "at least 32"),
        ({"cookie_secure": False}, "COOKIE_SECURE"),
    ],
)
def test_insecure_production_settings_fail(overrides: dict, expected: str) -> None:
    with pytest.raises(RuntimeError, match=expected):
        production_settings(**overrides).validate_production()


def test_development_settings_keep_local_defaults() -> None:
    production_settings(
        environment="development",
        database_url="sqlite:///data/journee.db",
        admin_password_hash="",
        session_secret="short",
        cookie_secure=False,
    ).validate_production()


def test_cockroach_cloud_url_uses_required_sqlalchemy_dialect(monkeypatch) -> None:
    monkeypatch.setenv(
        "LRC_DATABASE_URL",
        "postgresql://user:password@sample.cockroachlabs.cloud:26257/defaultdb?sslmode=verify-full",
    )
    assert load_settings().database_url.startswith("cockroachdb+psycopg://")


def test_database_connection_settings_can_be_overridden(monkeypatch) -> None:
    monkeypatch.setenv("LRC_DATABASE_POOL_SIZE", "4")
    monkeypatch.setenv("LRC_DATABASE_MAX_OVERFLOW", "0")
    monkeypatch.setenv("LRC_DATABASE_CONNECT_TIMEOUT_SECONDS", "3")
    settings = load_settings()
    assert settings.database_pool_size == 4
    assert settings.database_max_overflow == 0
    assert settings.database_connect_timeout_seconds == 3


def test_default_pool_wait_handles_venue_login_burst_without_more_connections(monkeypatch) -> None:
    monkeypatch.delenv("LRC_DATABASE_POOL_SIZE", raising=False)
    monkeypatch.delenv("LRC_DATABASE_MAX_OVERFLOW", raising=False)
    monkeypatch.delenv("LRC_DATABASE_POOL_TIMEOUT_SECONDS", raising=False)
    settings = load_settings()
    assert settings.database_pool_size == 5
    assert settings.database_max_overflow == 0
    assert settings.database_pool_timeout_seconds == 30


@pytest.mark.parametrize(("name", "value"), [
    ("LRC_DATABASE_POOL_SIZE", "0"),
    ("LRC_DATABASE_MAX_OVERFLOW", "-1"),
    ("LRC_DATABASE_POOL_TIMEOUT_SECONDS", "0"),
    ("LRC_DATABASE_CONNECT_TIMEOUT_SECONDS", "invalid"),
    ("LRC_DATABASE_POOL_RECYCLE_SECONDS", "-1"),
])
def test_database_connection_settings_reject_unbounded_values(monkeypatch, name, value) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        load_settings()
