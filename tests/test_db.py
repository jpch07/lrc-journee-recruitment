from __future__ import annotations

from sqlalchemy import create_engine, event
from dataclasses import replace

import pytest

from app import db

from app.db import (
    POSTGRES_SEARCH_PATH_SQL,
    _postgres_transaction_search_path,
    build_engine_options,
    configure_engine_events,
)


def test_sqlite_engine_options_allow_threaded_test_clients() -> None:
    options = build_engine_options("sqlite:///data/journee.db")

    assert options["pool_pre_ping"] is True
    assert options["connect_args"] == {"check_same_thread": False}
    assert "pool_size" not in options
    assert "max_overflow" not in options


def test_postgres_pool_does_not_use_unsupported_startup_options() -> None:
    options = build_engine_options("postgresql+psycopg://user:secret@example.invalid/lrc")

    assert options["pool_pre_ping"] is True
    assert options["connect_args"] == {"connect_timeout": 5}
    assert "options" not in options["connect_args"]
    assert options["pool_size"] == 5
    assert options["max_overflow"] == 0
    assert options["pool_timeout"] == 30
    assert options["pool_recycle"] == 300
    assert options["pool_use_lifo"] is True


@pytest.mark.parametrize("scheme", ["postgresql+psycopg", "cockroachdb+psycopg"])
def test_external_database_pool_limits_are_configurable(monkeypatch, scheme) -> None:
    monkeypatch.setattr(db, "settings", replace(
        db.settings, database_pool_size=3, database_max_overflow=1,
        database_pool_timeout_seconds=7, database_connect_timeout_seconds=4,
        database_pool_recycle_seconds=120,
    ))
    options = build_engine_options(f"{scheme}://user:secret@example.invalid/lrc")
    assert options["pool_size"] == 3
    assert options["max_overflow"] == 1
    assert options["pool_timeout"] == 7
    assert options["connect_args"]["connect_timeout"] == 4
    assert options["pool_recycle"] == 120


def test_postgres_search_path_is_configured_for_every_transaction() -> None:
    postgres_engine = create_engine("postgresql+psycopg://user:secret@example.invalid/lrc")

    configure_engine_events(postgres_engine, is_sqlite=False)

    assert event.contains(postgres_engine, "begin", _postgres_transaction_search_path)
    assert POSTGRES_SEARCH_PATH_SQL == "set local search_path to journee_recruitment, public"
