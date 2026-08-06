from __future__ import annotations

from sqlalchemy import create_engine, event

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


def test_postgres_pool_does_not_use_unsupported_startup_options() -> None:
    options = build_engine_options("postgresql+psycopg://user:secret@example.invalid/lrc")

    assert options["pool_pre_ping"] is True
    assert "connect_args" not in options


def test_postgres_search_path_is_configured_for_every_transaction() -> None:
    postgres_engine = create_engine("postgresql+psycopg://user:secret@example.invalid/lrc")

    configure_engine_events(postgres_engine, is_sqlite=False)

    assert event.contains(postgres_engine, "begin", _postgres_transaction_search_path)
    assert POSTGRES_SEARCH_PATH_SQL == "set local search_path to journee_recruitment, public"
