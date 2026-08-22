from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker, with_loader_criteria

from .config import settings


class Base(DeclarativeBase):
    pass


IS_SQLITE = settings.database_url.startswith("sqlite")
IS_COCKROACH = settings.database_url.startswith("cockroachdb") or "cockroachlabs.cloud" in settings.database_url
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


@event.listens_for(Session, "do_orm_execute")
def _scope_recruitment_queries(execute_state) -> None:
    """Keep every root query inside the recruitment selected for this request."""
    if execute_state.execution_options.get("bypass_recruitment_scope"):
        return
    from .models import AssessmentSystem, AuditEvent, EvaluatorDirectory, Journey, RecruitDirectory, RecruitDirectoryState, UserAccount
    from .tenant import current_system_id

    system_id = current_system_id()
    if not system_id:
        return
    statement = execute_state.statement.options(
        with_loader_criteria(AssessmentSystem, lambda row: row.id == system_id, include_aliases=True),
    )
    for model in (Journey, AuditEvent, EvaluatorDirectory, RecruitDirectory, RecruitDirectoryState, UserAccount):
        statement = statement.options(
            with_loader_criteria(model, lambda row: row.system_id == system_id, include_aliases=True),
        )
    execute_state.statement = statement


@event.listens_for(Session, "before_flush")
def _assign_recruitment_to_new_rows(session, _flush_context, _instances) -> None:
    from sqlalchemy import select
    from .models import AssessmentSystem, AuditEvent, EvaluatorDirectory, Journey, RecruitDirectory, RecruitDirectoryState, UserAccount
    from .tenant import current_system_id

    system_id = current_system_id()
    if not system_id:
        available = list(session.scalars(
            select(AssessmentSystem.id).limit(2).execution_options(bypass_recruitment_scope=True)
        ))
        system_id = available[0] if len(available) == 1 else None
    scoped_models = (Journey, AuditEvent, EvaluatorDirectory, RecruitDirectory, RecruitDirectoryState, UserAccount)
    needs_system = any(isinstance(row, scoped_models) and not row.system_id for row in session.new)
    if not system_id and needs_system:
        # Service-level and unit-test callers may use a fresh database without the
        # HTTP lifespan. Bootstrap the compatibility recruitment only in that case.
        from .assessment_config import lrc_assessment_definition
        from .assessment_service import system_slug
        from .models import AssessmentSystemVersion, new_id
        from .utils import dumps

        definition = lrc_assessment_definition()
        system_id = new_id()
        definition_json = dumps(definition.model_dump(mode="json"))
        session.add(AssessmentSystem(
            id=system_id, name=definition.name, slug=system_slug(definition.name),
            draft_json=definition_json, published_version=1, updated_by="System bootstrap",
        ))
        session.add(AssessmentSystemVersion(
            system_id=system_id, version=1, definition_json=definition_json,
            change_summary="Compatibility bootstrap.", published_by="System bootstrap",
        ))
    if not system_id:
        return
    for row in session.new:
        if isinstance(row, scoped_models) and not row.system_id:
            row.system_id = system_id


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
        if IS_COCKROACH:
            connection.execute(text(
                "create table if not exists journee_recruitment.deployment_migration_lock "
                "(id integer primary key, holder varchar(200), updated_at timestamptz default current_timestamp)"
            ))
            connection.execute(text(
                "insert into journee_recruitment.deployment_migration_lock (id, holder) values (1, 'alembic') "
                "on conflict (id) do nothing"
            ))
            connection.execute(text(
                "select id from journee_recruitment.deployment_migration_lock where id=1 for update"
            ))
        if not IS_SQLITE and not IS_COCKROACH:
            connection.execute(text("select pg_advisory_lock(hashtext('lrc_journee_migrations'))"))
        try:
            configuration.attributes["connection"] = connection
            command.upgrade(configuration, "head")
        finally:
            if not IS_SQLITE and not IS_COCKROACH:
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
