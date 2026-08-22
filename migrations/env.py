from __future__ import annotations

from logging.config import fileConfig

from alembic import context

from app.config import settings
from app.db import Base
from app import models  # noqa: F401


config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def _context_options() -> dict:
    options = {"target_metadata": target_metadata, "compare_type": True}
    if settings.database_url.startswith("cockroachdb") or "cockroachlabs.cloud" in settings.database_url:
        # Cockroach's dialect does not reliably resolve Alembic's checkfirst
        # probe through search_path after non-transactional DDL. Qualifying the
        # version table prevents a false attempt to create it again.
        options["version_table_schema"] = "journee_recruitment"
    return options


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        literal_binds=True,
        **_context_options(),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is None:
        from app.db import engine

        with engine.connect() as connection:
            context.configure(connection=connection, **_context_options())
            with context.begin_transaction():
                context.run_migrations()
    else:
        context.configure(connection=connection, **_context_options())
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
