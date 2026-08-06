from __future__ import annotations

from app.db import build_engine_options


def test_sqlite_engine_options_allow_threaded_test_clients() -> None:
    options = build_engine_options("sqlite:///data/journee.db")

    assert options["pool_pre_ping"] is True
    assert options["connect_args"] == {"check_same_thread": False}


def test_postgres_search_path_is_set_during_connection_handshake() -> None:
    options = build_engine_options("postgresql+psycopg://user:secret@example.invalid/lrc")

    assert options["pool_pre_ping"] is True
    assert options["connect_args"] == {
        "options": "-csearch_path=journee_recruitment,public",
    }
