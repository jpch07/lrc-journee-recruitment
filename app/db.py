from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


IS_SQLITE = settings.database_url.startswith("sqlite")
engine_options: dict = {"pool_pre_ping": True}
if IS_SQLITE:
    engine_options["connect_args"] = {"check_same_thread": False}

engine = create_engine(settings.database_url, **engine_options)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


if IS_SQLITE:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()
else:
    @event.listens_for(engine, "connect")
    def _postgres_search_path(dbapi_connection, _connection_record):
        with dbapi_connection.cursor() as cursor:
            cursor.execute("set search_path to journee_recruitment, public")


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
