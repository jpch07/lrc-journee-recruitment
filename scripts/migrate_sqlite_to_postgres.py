"""Copy selected Journees from the local SQLite database to empty PostgreSQL storage.

The destination URL is read from LRC_DATABASE_URL so credentials never need to be
placed on the command line. Runtime sessions and idempotency keys are intentionally
not copied. The utility refuses to write when the Journee schema already contains
data, making accidental merges impossible.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from collections import OrderedDict
from contextlib import closing
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.pool import NullPool


ROOT = Path(__file__).resolve().parents[1]
SKIPPED_TABLES = {"admin_sessions", "evaluator_sessions", "idempotency_records"}


def snapshot_sqlite(source: Path, destination: Path) -> None:
    """Use SQLite's online backup API so an active WAL cannot produce a partial copy."""
    with closing(sqlite3.connect(source)) as source_db, closing(sqlite3.connect(destination)) as destination_db:
        source_db.backup(destination_db)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate selected local Journees into an empty production PostgreSQL schema."
    )
    parser.add_argument("--source", type=Path, default=ROOT / "data" / "journee.db")
    parser.add_argument(
        "--journey",
        action="append",
        default=[],
        help="Exact Journee name or UUID to copy. Repeat for multiple Journees.",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Perform the write. Without this flag, only the migration plan is displayed.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_path = args.source.resolve()
    if not source_path.is_file():
        raise SystemExit(f"SQLite source does not exist: {source_path}")

    target_url = os.getenv("LRC_DATABASE_URL", "")
    if not target_url:
        raise SystemExit("Set LRC_DATABASE_URL to the destination PostgreSQL connection string.")
    if target_url.startswith("sqlite"):
        raise SystemExit("LRC_DATABASE_URL must be PostgreSQL, not SQLite.")

    # Import only after validating LRC_DATABASE_URL: app settings are resolved at import time.
    sys.path.insert(0, str(ROOT))
    from app import models  # noqa: F401
    from app.db import Base, engine as target_engine, initialize_database

    with tempfile.TemporaryDirectory(prefix="lrc-journee-migration-") as temporary:
        snapshot_path = Path(temporary) / "source.db"
        snapshot_sqlite(source_path, snapshot_path)
        # NullPool closes every SQLite handle immediately; this matters on Windows,
        # where an open pooled handle prevents deletion of the temporary snapshot.
        source_engine = create_engine(f"sqlite:///{snapshot_path.as_posix()}", poolclass=NullPool)

        journeys_table = Base.metadata.tables["journeys"]
        with source_engine.connect() as connection:
            available = list(connection.execute(select(journeys_table)).mappings())
        if not available:
            raise SystemExit("The source database contains no Journees.")

        requested = set(args.journey)
        selected = [
            row for row in available
            if not requested or row["id"] in requested or row["name"] in requested
        ]
        if requested:
            matched = {row["id"] for row in selected} | {row["name"] for row in selected}
            missing = sorted(item for item in requested if item not in matched)
            if missing:
                raise SystemExit("Journee not found: " + ", ".join(missing))
        selected_ids = {row["id"] for row in selected}

        # Delete excluded Journees from the isolated snapshot. Cascades remove all
        # dependent operational data while leaving the reusable evaluator directory.
        with closing(sqlite3.connect(snapshot_path)) as snapshot_db:
            snapshot_db.execute("PRAGMA foreign_keys=ON")
            for row in available:
                if row["id"] not in selected_ids:
                    snapshot_db.execute("DELETE FROM journeys WHERE id = ?", (row["id"],))
            snapshot_db.commit()

        counts: OrderedDict[str, int] = OrderedDict()
        with source_engine.connect() as connection:
            for table in Base.metadata.sorted_tables:
                if table.name in SKIPPED_TABLES:
                    continue
                counts[table.name] = connection.execute(
                    select(func.count()).select_from(table)
                ).scalar_one()

        print("Selected Journees:")
        for row in selected:
            print(f"- {row['name']} ({row['event_date']}, {row['status']})")
        print("Rows to copy:")
        for table_name, count in counts.items():
            print(f"- {table_name}: {count}")
        print("- runtime sessions/idempotency keys: excluded")
        if not args.confirm:
            print("Dry run only. Re-run with --confirm to write to PostgreSQL.")
            source_engine.dispose()
            return

        initialize_database()
        with target_engine.begin() as target_connection:
            existing = target_connection.execute(
                select(func.count()).select_from(journeys_table)
            ).scalar_one()
            if existing:
                raise SystemExit(
                    "Destination journee_recruitment schema is not empty; migration refused."
                )
            with source_engine.connect() as source_connection:
                for table in Base.metadata.sorted_tables:
                    if table.name in SKIPPED_TABLES:
                        continue
                    rows = list(source_connection.execute(select(table)).mappings())
                    if rows:
                        target_connection.execute(table.insert(), [dict(row) for row in rows])

        failures: list[str] = []
        with target_engine.connect() as connection:
            for table in Base.metadata.sorted_tables:
                if table.name in SKIPPED_TABLES:
                    continue
                actual = connection.execute(select(func.count()).select_from(table)).scalar_one()
                if actual != counts[table.name]:
                    failures.append(f"{table.name}: expected {counts[table.name]}, got {actual}")
        if failures:
            raise SystemExit("Migration verification failed: " + "; ".join(failures))
        source_engine.dispose()
        print("Migration completed and all table counts were verified.")


if __name__ == "__main__":
    main()
