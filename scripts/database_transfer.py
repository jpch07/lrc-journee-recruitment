"""Lossless, fail-closed logical backups and transactional PostgreSQL restores.

URLs come only from EVALDAY_SOURCE_DATABASE_URL / EVALDAY_TARGET_DATABASE_URL.
Archives contain confidential records and managed passwords: store them privately.
No source writes, deletions, automatic migrations, billing changes, or deployment.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import UUID
import zipfile

from sqlalchemy import create_engine, event, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "journee_recruitment"
FORMAT = "evalday-logical-backup-v1"
OPERATIONAL_TABLES = {"alembic_version", "deployment_migration_lock"}
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TransferError(RuntimeError):
    """Safe-to-display error; never include row contents or connection strings."""


def metadata():
    from app import models  # noqa: F401
    from app.db import Base
    return Base.metadata


def current_revision():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return ScriptDirectory.from_config(config).get_current_head()


def encode(value):
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytes):
        return {"$type": "bytes", "value": base64.b64encode(value).decode("ascii")}
    if isinstance(value, datetime):
        # Equal instants may have different PostgreSQL session timezone offsets.
        # SQLite discards timezone information from DateTime(timezone=True).
        # Evalday stores UTC timestamps and interprets those naive values as UTC;
        # use the same convention so native PostgreSQL restores compare exactly.
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc)
        return {"$type": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"$type": "date", "value": value.isoformat()}
    if isinstance(value, Decimal):
        return {"$type": "decimal", "value": str(value)}
    if isinstance(value, UUID):
        return {"$type": "uuid", "value": str(value)}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TransferError("Unsupported database value type; export stopped without data loss.")


def decode(value):
    if not isinstance(value, dict):
        return value
    if set(value) != {"$type", "value"}:
        raise TransferError("Invalid typed value in archive.")
    decoders = {"bytes": lambda x: base64.b64decode(x, validate=True),
                "datetime": datetime.fromisoformat, "date": date.fromisoformat,
                "decimal": Decimal, "uuid": UUID}
    if value["$type"] not in decoders:
        raise TransferError("Unknown typed value in archive.")
    return decoders[value["$type"]](value["value"])


def json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def row_bytes(row):
    return json_bytes({key: encode(value) for key, value in row.items()}) + b"\n"


def engine_for(url):
    parsed = make_url(url)
    if parsed.drivername in {"postgres", "postgresql", "cockroachdb"}:
        driver = "cockroachdb" if ("cockroachlabs.cloud" in (parsed.host or "") or
                                  parsed.drivername == "cockroachdb") else "postgresql"
        parsed = parsed.set(drivername=driver + "+psycopg")
    if parsed.get_backend_name() not in {"sqlite", "postgresql", "cockroachdb"}:
        raise TransferError("Only PostgreSQL, CockroachDB, and local SQLite snapshots are supported.")
    options = {"poolclass": NullPool, "hide_parameters": True}
    if parsed.get_backend_name() != "sqlite":
        options["connect_args"] = {"connect_timeout": 10}
    engine = create_engine(parsed, **options)
    if parsed.get_backend_name() == "sqlite":
        @event.listens_for(engine, "connect")
        def sqlite_fk(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")
    else:
        @event.listens_for(engine, "begin")
        def schema_path(connection):
            connection.exec_driver_sql(f"SET LOCAL search_path TO {SCHEMA}, public")
    return engine


@contextmanager
def read_snapshot(engine):
    with engine.connect() as connection:
        if engine.dialect.name == "postgresql":
            connection = connection.execution_options(isolation_level="REPEATABLE READ")
        with connection.begin():
            if engine.dialect.name != "sqlite":
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            yield connection


def assert_schema(connection, *, allow_missing=False):
    inspector = inspect(connection)
    schema = None if connection.dialect.name == "sqlite" else SCHEMA
    actual = set(inspector.get_table_names(schema=schema))
    expected = set(metadata().tables)
    if actual - expected - OPERATIONAL_TABLES:
        raise TransferError("Unrecognized tables exist; refusing an incomplete backup or restore.")
    if not allow_missing and expected - actual:
        raise TransferError("Required application tables are missing; use a matching application revision.")
    for name in actual & expected:
        columns = {col["name"] for col in inspector.get_columns(name, schema=schema)}
        if columns != set(metadata().tables[name].c.keys()):
            raise TransferError(f"Column mismatch in {name}; refusing to silently discard data.")
    if connection.dialect.name != "sqlite":
        # The current application uses UUID primary keys, not sequences. A future
        # serial/identity schema must add sequence-state support before migration.
        if inspector.get_sequence_names(schema=schema):
            raise TransferError("Unexpected sequences found; sequence-aware migration is required.")
    return actual


def revision_at(connection):
    return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def sorted_rows(connection, table, *, seed=None):
    # Sorting primary keys makes hashes independent of physical database order.
    statement = select(table).order_by(*table.primary_key.columns)
    if seed is not None:
        if table.name == "assessment_systems":
            statement = statement.where(table.c.id == seed["systemId"])
        elif table.name in {"assessment_system_versions", "evaluator_directory", "user_accounts"}:
            statement = statement.where(table.c.system_id == seed["systemId"])
        elif table.name == "platform_accounts":
            statement = statement.where(table.c.id.in_(seed["platformAccountIds"]))
        else:
            return iter(())
    return connection.execute(statement).mappings()


def workspace_seed(connection, slug):
    tables = metadata().tables
    workspace = connection.execute(select(tables["assessment_systems"]).where(
        tables["assessment_systems"].c.slug == slug)).mappings().one_or_none()
    if workspace is None:
        raise TransferError("No workspace has that exact slug; seed export stopped.")
    account_ids = set(connection.execute(select(tables["user_accounts"].c.platform_account_id).where(
        tables["user_accounts"].c.system_id == workspace["id"])).scalars())
    account_ids.add(workspace["owner_platform_account_id"])
    account_ids.discard(None)
    return {"systemId": workspace["id"], "platformAccountIds": sorted(account_ids)}


def table_signature(connection, table):
    digest, count = hashlib.sha256(), 0
    for row in sorted_rows(connection, table):
        digest.update(row_bytes(row))
        count += 1
    return {"rows": count, "sha256": digest.hexdigest(), "columns": list(table.c.keys())}


def assert_empty(connection):
    actual = assert_schema(connection, allow_missing=True)
    for table in metadata().tables.values():
        if table.name in actual and connection.execute(select(table).limit(1)).first() is not None:
            raise TransferError(f"Destination is not empty ({table.name}); no records were overwritten.")
    return actual


def export_database(engine, output: Path, *, workspace_slug=None):
    """Export all application rows, including sessions and idempotency records."""
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidentally overwriting the only good backup.
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    manifest = {"format": FORMAT, "createdAt": datetime.now(timezone.utc).isoformat(),
                "kind": "workspace-seed" if workspace_slug else "full",
                "tables": {}, "excludedOperationalTables": sorted(OPERATIONAL_TABLES),
                "externalPhotos": "Object keys/checksums retained; back up R2 objects separately.",
                "containsSecrets": True,
                "requiredExternalConfiguration": ["LRC_JOURNEE_SESSION_SECRET", "LRC_R2_*",
                                                  "LRC_RECRUIT_SHEET_*", "LRC_JOURNEE_ADMIN_PASSWORD_HASH"]}
    with os.fdopen(descriptor, "wb") as stream, zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        with read_snapshot(engine) as source:
            assert_schema(source)
            manifest["revision"] = revision_at(source)
            if manifest["revision"] != current_revision():
                raise TransferError("Source revision differs from checked-out code; export refused.")
            seed = workspace_seed(source, workspace_slug) if workspace_slug else None
            if workspace_slug:
                manifest["workspaceSlug"] = workspace_slug
            for table in metadata().sorted_tables:
                digest, count = hashlib.sha256(), 0
                with archive.open(f"tables/{table.name}.jsonl", "w") as target:
                    for row in sorted_rows(source, table, seed=seed):
                        encoded = row_bytes(row)
                        target.write(encoded)
                        digest.update(encoded)
                        count += 1
                manifest["tables"][table.name] = {"rows": count, "sha256": digest.hexdigest(),
                                                  "columns": list(table.c.keys())}
            manifest["dataSha256"] = hashlib.sha256(json_bytes(manifest["tables"])).hexdigest()
            # Manifest is deliberately written last. Interrupted exports cannot restore.
            archive.writestr("manifest.json", json_bytes(manifest))
    return manifest


def validate_archive(archive):
    if "manifest.json" not in archive.namelist():
        raise TransferError("Incomplete backup: final manifest is absent.")
    manifest = json.loads(archive.read("manifest.json"))
    if manifest.get("format") != FORMAT or manifest.get("revision") != current_revision():
        raise TransferError("Archive format/revision does not match this application.")
    tables = metadata().tables
    if set(manifest["tables"]) != set(tables):
        raise TransferError("Archive does not contain exactly all application tables.")
    if manifest.get("dataSha256") != hashlib.sha256(json_bytes(manifest["tables"])).hexdigest():
        raise TransferError("Archive manifest integrity check failed.")
    expected_files = {"manifest.json"} | {f"tables/{name}.jsonl" for name in tables}
    if len(archive.namelist()) != len(expected_files) or set(archive.namelist()) != expected_files:
        raise TransferError("Unexpected or duplicate archive entries.")
    for name, table in tables.items():
        expected = manifest["tables"][name]
        if expected["columns"] != list(table.c.keys()):
            raise TransferError(f"Archive columns differ for {name}.")
        digest, count = hashlib.sha256(), 0
        with archive.open(f"tables/{name}.jsonl") as stream:
            for line in stream:
                digest.update(line)
                row = json.loads(line)
                if set(row) != set(table.c.keys()):
                    raise TransferError(f"Incomplete row in {name}.")
                for value in row.values():
                    decode(value)
                count += 1
        if digest.hexdigest() != expected["sha256"] or count != expected["rows"]:
            raise TransferError(f"Archive checksum/count mismatch for {name}.")
    return manifest


def ordered_self_references(table, rows):
    self_references = [fk for fk in table.foreign_keys if fk.column.table.name == table.name]
    if not self_references:
        return rows
    pending = list(rows)
    ordered = []
    while pending:
        ready = [row for row in pending if all(
            row[fk.parent.name] is None or
            row[fk.parent.name] == row[fk.column.name] or
            not any(parent[fk.column.name] == row[fk.parent.name] for parent in pending)
            for fk in self_references)]
        if not ready:
            raise TransferError(f"Circular self-reference in {table.name}; restore aborted.")
        ready_ids = {id(row) for row in ready}
        pending = [row for row in pending if id(row) not in ready_ids]
        ordered.extend(ready)
    return ordered


def verify_connection(connection, manifest):
    assert_schema(connection)
    if revision_at(connection) != manifest["revision"]:
        raise TransferError("Database revision does not match archive.")
    for table in metadata().sorted_tables:
        if table_signature(connection, table) != manifest["tables"][table.name]:
            raise TransferError(f"Full-row parity failed for {table.name}.")


def restore_database(engine, backup: Path, *, confirm=False, initialize_empty=False, allow_workspace_seed=False):
    if engine.dialect.name not in {"postgresql", "sqlite"}:
        raise TransferError("Transactional restore supports PostgreSQL only (SQLite for local rehearsal).")
    with zipfile.ZipFile(backup) as archive:
        manifest = validate_archive(archive)
        if manifest.get("kind") == "workspace-seed" and not allow_workspace_seed:
            raise TransferError("Account-only workspace seed is not a full backup; use --allow-workspace-seed explicitly.")
        if manifest.get("kind") not in {"full", "workspace-seed"}:
            raise TransferError("Archive purpose is unknown; restore refused.")
        with engine.begin() as target:
            if engine.dialect.name == "postgresql":
                # One transaction-scoped advisory lock serializes cooperating restores.
                target.execute(text("SELECT pg_advisory_xact_lock(hashtext('evalday_logical_restore'))"))
                # Avoid shadowing current application tables accidentally held in public.
                public = set(inspect(target).get_table_names(schema="public"))
                if public & set(metadata().tables):
                    raise TransferError("Application tables exist in public; resolve schema ambiguity first.")
            actual = assert_empty(target)
            if not confirm:
                return {**manifest, "dryRun": True}
            if initialize_empty:
                if engine.dialect.name == "postgresql":
                    target.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
                metadata().create_all(target)
                target.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
                existing = list(target.execute(text("SELECT version_num FROM alembic_version")).scalars())
                if existing and existing != [manifest["revision"]]:
                    raise TransferError("Empty destination has a different migration revision.")
                if not existing:
                    target.execute(text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                                   {"revision": manifest["revision"]})
            elif set(metadata().tables) - actual:
                raise TransferError("Target schema is missing; use --initialize-empty after dry-run review.")
            assert_schema(target)
            if revision_at(target) != manifest["revision"]:
                raise TransferError("Destination migration revision differs.")
            if engine.dialect.name == "postgresql":
                # Prevent concurrent app writes between the empty check and import.
                names = ", ".join(f'{SCHEMA}."{name}"' for name in metadata().tables)
                target.exec_driver_sql(f"LOCK TABLE {names} IN ACCESS EXCLUSIVE MODE")
                assert_empty(target)
            for table in metadata().sorted_tables:
                with archive.open(f"tables/{table.name}.jsonl") as stream:
                    rows = ({key: decode(value) for key, value in json.loads(line).items()} for line in stream)
                    self_reference = any(fk.column.table.name == table.name for fk in table.foreign_keys)
                    if self_reference:
                        # Parents must already exist even if their primary key
                        # sorts after a child in the deterministic archive.
                        for row in ordered_self_references(table, list(rows)):
                            target.execute(table.insert(), row)
                    else:
                        # Bound memory and round trips. Photos remain one-row
                        # inserts so a batch cannot create an oversized frame.
                        batch_size = 1 if table.name == "recruits" else 100
                        batch = []
                        for row in rows:
                            batch.append(row)
                            if len(batch) >= batch_size:
                                target.execute(table.insert(), batch)
                                batch = []
                        if batch:
                            target.execute(table.insert(), batch)
            # Compare every typed value before commit. Any difference rolls back.
            verify_connection(target, manifest)
        return {**manifest, "dryRun": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["inspect", "export", "restore", "verify"])
    parser.add_argument("archive", type=Path, nargs="?")
    parser.add_argument("--confirm", action="store_true", help="Required to write destination rows.")
    parser.add_argument("--initialize-empty", action="store_true", help="Create the current schema in an empty destination.")
    parser.add_argument("--workspace-slug", help="Export only this workspace's configuration and accounts; no event history.")
    parser.add_argument("--allow-workspace-seed", action="store_true", help="Explicitly permit an account-only seed restore.")
    args = parser.parse_args()
    env = "EVALDAY_SOURCE_DATABASE_URL" if args.operation in {"inspect", "export"} else "EVALDAY_TARGET_DATABASE_URL"
    url = os.environ.get(env, "").strip()
    if not url:
        raise TransferError(f"Set {env}; connection strings are never accepted on the command line.")
    if args.operation != "inspect" and not args.archive:
        parser.error("This operation requires an archive path.")
    if args.workspace_slug and args.operation != "export":
        parser.error("--workspace-slug is only valid for export.")
    engine = engine_for(url)
    try:
        if args.operation == "inspect":
            with read_snapshot(engine) as connection:
                assert_schema(connection)
                print(f"Revision: {revision_at(connection)}")
                for table in metadata().sorted_tables:
                    count = connection.execute(select(func.count()).select_from(table)).scalar_one()
                    print(f"{table.name}: {count}")
            return
        if args.operation == "export":
            result = export_database(engine, args.archive, workspace_slug=args.workspace_slug)
        elif args.operation == "restore":
            result = restore_database(engine, args.archive, confirm=args.confirm, initialize_empty=args.initialize_empty,
                                      allow_workspace_seed=args.allow_workspace_seed)
        else:
            with zipfile.ZipFile(args.archive) as archive:
                result = validate_archive(archive)
            with read_snapshot(engine) as connection:
                verify_connection(connection, result)
        print(json.dumps({"operation": args.operation, "dryRun": result.get("dryRun", False),
                          "revision": result["revision"], "tables": len(result["tables"]),
                          "rows": sum(item["rows"] for item in result["tables"].values()),
                          "dataSha256": result["dataSha256"]}))
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except TransferError as exc:
        print(f"Stopped safely: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:
        # SQLAlchemy exceptions can include passwords, raw records, or connection
        # URIs. Print their class only; never emit a full traceback from this CLI.
        print(f"Stopped safely ({type(exc).__name__}); no secret-bearing diagnostic was printed.", file=sys.stderr)
        raise SystemExit(1)
