"""Copy the complete recruitment schema from Neon/PostgreSQL to empty CockroachDB.

Environment:
  LRC_SOURCE_DATABASE_URL  existing Neon/PostgreSQL database
  LRC_DATABASE_URL         empty CockroachDB target

The source and target must both be on the current Alembic revision. The command
is a dry run unless --confirm is supplied. Runtime sessions and idempotency rows
are deliberately excluded so every user signs in again after cutover.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import OrderedDict
from pathlib import Path

from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.pool import NullPool


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SKIP = {"admin_sessions", "evaluator_sessions", "user_sessions", "platform_sessions", "idempotency_records"}


def normalized(url: str) -> str:
    if "cockroachlabs.cloud" in url and url.startswith("postgresql://"):
        return "cockroachdb+psycopg://" + url[len("postgresql://"):]
    if url.startswith("cockroachdb://"):
        return "cockroachdb+psycopg://" + url[len("cockroachdb://"):]
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def scoped_engine(url: str):
    engine = create_engine(normalized(url), poolclass=NullPool, pool_pre_ping=True)
    @event.listens_for(engine, "begin")
    def _search_path(connection):
        connection.exec_driver_sql("set local search_path to journee_recruitment, public")
    return engine


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    source_url = os.getenv("LRC_SOURCE_DATABASE_URL", "").strip()
    target_url = os.getenv("LRC_DATABASE_URL", "").strip()
    if not source_url or not target_url or source_url == target_url:
        raise SystemExit("Set different LRC_SOURCE_DATABASE_URL (Neon) and LRC_DATABASE_URL (CockroachDB).")

    sys.path.insert(0, str(ROOT))
    from app.db import Base, engine as target_engine, initialize_database
    from app import models  # noqa: F401

    source_engine = scoped_engine(source_url)
    source_revision = target_revision = None
    with source_engine.connect() as connection:
        source_revision = connection.execute(text("select version_num from alembic_version")).scalar_one()
    if not args.confirm:
        with source_engine.connect() as connection:
            counts = OrderedDict((table.name, connection.execute(select(func.count()).select_from(table)).scalar_one())
                                 for table in Base.metadata.sorted_tables if table.name not in SKIP)
        print(f"Source revision: {source_revision}")
        for name, count in counts.items():
            print(f"- {name}: {count}")
        print("Dry run only. Re-run with --confirm after backing up Neon.")
        return 0

    initialize_database()
    with target_engine.connect() as connection:
        target_revision = connection.execute(text("select version_num from alembic_version")).scalar_one()
    if source_revision != target_revision:
        raise SystemExit(f"Migration revisions differ: source={source_revision}, target={target_revision}")

    counts: OrderedDict[str, int] = OrderedDict()
    with source_engine.connect() as source, target_engine.begin() as target:
        if target.execute(select(func.count()).select_from(Base.metadata.tables["journeys"])).scalar_one():
            raise SystemExit("Target contains Journees; migration refused.")
        for table in Base.metadata.sorted_tables:
            if table.name in SKIP:
                continue
            rows = [dict(row) for row in source.execute(select(table)).mappings()]
            counts[table.name] = len(rows)
            if rows:
                target.execute(table.insert(), rows)

    failures = []
    with target_engine.connect() as target:
        for table in Base.metadata.sorted_tables:
            if table.name in SKIP:
                continue
            actual = target.execute(select(func.count()).select_from(table)).scalar_one()
            if actual != counts[table.name]:
                failures.append(f"{table.name}: expected {counts[table.name]}, got {actual}")
    if failures:
        raise SystemExit("Count verification failed: " + "; ".join(failures))
    print(f"Migration complete at revision {target_revision}; every table count matches.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
