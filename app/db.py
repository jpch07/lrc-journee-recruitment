from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


IS_SQLITE = settings.database_url.startswith("sqlite")
POSTGRES_SEARCH_PATH_SQL = "set local search_path to journee_recruitment, public"


def build_engine_options(database_url: str) -> dict:
    """Return connection options that are safe for SQLite and PostgreSQL pools.

    PostgreSQL schema selection is configured per transaction below because
    Neon/PgBouncer pooled endpoints reject ``search_path`` startup parameters.
    """
    options: dict = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
    return options


engine_options = build_engine_options(settings.database_url)

def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def _postgres_transaction_search_path(connection) -> None:
    # SET LOCAL is transaction-scoped, which is safe with transaction-pooling:
    # PgBouncer keeps every statement in this transaction on one server.
    connection.exec_driver_sql(POSTGRES_SEARCH_PATH_SQL)


def configure_engine_events(database_engine, *, is_sqlite: bool) -> None:
    if is_sqlite:
        event.listen(database_engine, "connect", _sqlite_pragmas)
    else:
        event.listen(database_engine, "begin", _postgres_transaction_search_path)


engine = create_engine(settings.database_url, **engine_options)
configure_engine_events(engine, is_sqlite=IS_SQLITE)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def initialize_database() -> None:
    from . import models  # noqa: F401
    from alembic import command
    from alembic.config import Config
    from .config import ROOT_DIR

    configuration = Config(str(ROOT_DIR / "alembic.ini"))
    configuration.set_main_option("script_location", str(ROOT_DIR / "migrations"))
    with engine.begin() as connection:
        if not IS_SQLITE:
            connection.execute(text("create schema if not exists journee_recruitment"))
            connection.execute(text("select pg_advisory_lock(hashtext('lrc_journee_migrations'))"))
        try:
            configuration.attributes["connection"] = connection
            command.upgrade(configuration, "head")
        finally:
            if not IS_SQLITE:
                connection.execute(text("select pg_advisory_unlock(hashtext('lrc_journee_migrations'))"))


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
