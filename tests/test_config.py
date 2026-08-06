from __future__ import annotations

import pytest

from app.config import Settings


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
