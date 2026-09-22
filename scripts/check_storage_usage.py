"""Report measured storage against explicit budgets; never treat errors as zero.

PostgreSQL reports physical bytes for the connected database, not total service
disk usage. Cockroach reports approximate application logical bytes. Provider
dashboards remain authoritative for service quotas, overhead, and billing.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings
from app.db import SessionLocal
from app.object_storage import _client, enabled


COCKROACH_ALLOWANCE = 10 * 1024**3
AIVEN_ALLOWANCE = 1_000_000_000
R2_ALLOWANCE = 10 * 1024**3


class StorageCheckError(RuntimeError):
    """Safe, credential-free diagnostic intended for monitor output."""


def level(percent: float) -> str:
    if percent >= 90:
        return "CRITICAL"
    if percent >= 75:
        return "WARNING"
    if percent >= 50:
        return "NOTICE"
    return "OK"


def database_kind(database_url: str) -> str:
    parsed = make_url(database_url)
    if parsed.get_backend_name() == "cockroachdb" or (parsed.host or "").endswith(".cockroachlabs.cloud"):
        return "cockroachdb"
    if parsed.get_backend_name() in {"postgres", "postgresql"}:
        return "postgresql"
    raise StorageCheckError("Storage checks require PostgreSQL or CockroachDB.")


def configured_allowance(database_url: str, override: int | None = None) -> tuple[int, str]:
    if override is not None:
        if override <= 0:
            raise StorageCheckError("The database allowance must be a positive number of bytes.")
        return override, "explicit budget"
    parsed = make_url(database_url)
    if (parsed.host or "").endswith(".aivencloud.com"):
        return AIVEN_ALLOWANCE, "Aiven Free default budget (1 GB; confirm the service plan)"
    if database_kind(database_url) == "cockroachdb":
        return COCKROACH_ALLOWANCE, "nominal 10 GiB budget, not a verified cluster/account quota"
    raise StorageCheckError("Set --database-allowance-bytes or EVALDAY_DATABASE_ALLOWANCE_BYTES for this provider.")


def database_logical_bytes(session_factory=None) -> int:
    """Cockroach compatibility estimate; any table-query failure aborts the check."""
    session_factory = session_factory or SessionLocal
    total = 0
    with session_factory() as db:
        names = [row[0] for row in db.execute(text(
            "select table_name from information_schema.tables "
            "where table_schema='journee_recruitment' and table_type='BASE TABLE'"
        ))]
        if not names:
            raise StorageCheckError("No application tables are visible; logical storage usage is unknown.")
        for name in names:
            quoted = name.replace('"', '""')
            measured = db.execute(text(
                f'select coalesce(sum(pg_column_size(t)),0) from journee_recruitment."{quoted}" t'
            )).scalar_one()
            if measured is None or int(measured) < 0:
                raise StorageCheckError("Invalid logical storage measurement; usage is unknown.")
            total += int(measured)
    return total


def database_measurement(database_url: str, session_factory=None) -> dict:
    session_factory = session_factory or SessionLocal
    kind = database_kind(database_url)
    if kind == "cockroachdb":
        return {"providerKind": kind, "bytes": database_logical_bytes(session_factory),
                "measurement": "approximate application logical row bytes",
                "scope": "journee_recruitment schema only; excludes physical indexes, replication, compression and other databases"}
    with session_factory() as db:
        name, measured = db.execute(text(
            "SELECT current_database(), pg_database_size(current_database())"
        )).one()
    if measured is None or int(measured) < 0:
        raise StorageCheckError("Invalid physical storage measurement; usage is unknown.")
    return {"providerKind": kind, "database": name, "bytes": int(measured),
            "measurement": "physical current-database bytes (tables, indexes and TOAST)",
            "scope": "current database only; excludes other databases, WAL, replication and service overhead; not total cluster quota usage"}


def r2_bytes() -> tuple[int, int] | None:
    if not enabled():
        return None
    client = _client()
    count = total = 0
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=settings.r2_bucket):
        for item in page.get("Contents", []):
            count += 1
            if "Size" not in item or int(item["Size"]) < 0:
                raise StorageCheckError("An R2 object size is unknown; storage check is incomplete.")
            total += int(item["Size"])
    return count, total


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fail-at", type=float, default=90)
    parser.add_argument("--database-allowance-bytes", type=int, default=os.getenv("EVALDAY_DATABASE_ALLOWANCE_BYTES"))
    parser.add_argument("--r2-allowance-bytes", type=int, default=os.getenv("EVALDAY_R2_ALLOWANCE_BYTES", str(R2_ALLOWANCE)))
    parser.add_argument("--json", action="store_true", help="Machine-readable report without credentials.")
    args = parser.parse_args(argv)
    try:
        if not 0 < args.fail_at <= 100 or args.r2_allowance_bytes <= 0:
            raise StorageCheckError("Threshold must be between 0 and 100; storage budgets must be positive.")
        allowance, allowance_source = configured_allowance(settings.database_url, args.database_allowance_bytes)
        database = database_measurement(settings.database_url)
        database.update(allowanceBytes=allowance, allowanceSource=allowance_source)
        database["percent"] = database["bytes"] / allowance * 100
        database["level"] = level(database["percent"])
        measured_r2 = r2_bytes()
        r2 = {"status": "not configured", "scope": "No R2 measurement was performed."}
        percentages = [database["percent"]]
        if measured_r2 is not None:
            object_count, object_size = measured_r2
            percent = object_size / args.r2_allowance_bytes * 100
            r2 = {"status": "measured", "objects": object_count, "bytes": object_size,
                  "allowanceBytes": args.r2_allowance_bytes, "percent": percent, "level": level(percent),
                  "scope": "configured bucket's current object bytes only; excludes other buckets, monthly GB-month averaging and request quotas"}
            percentages.append(percent)
        exceeded = max(percentages) >= args.fail_at
        report = {"status": "threshold reached" if exceeded else "measured", "database": database,
                  "r2": r2, "providerDashboardRequired": True}
        if args.json:
            print(json.dumps(report))
        else:
            print(f"Database {database['measurement']}: {database['bytes']:,} bytes "
                  f"({database['percent']:.2f}% of {allowance:,}) [{database['level']}]")
            print(f"Database scope: {database['scope']}")
            print(f"Budget: {allowance_source}")
            if measured_r2 is None:
                print("R2: not configured (unknown, not zero usage).")
            else:
                print(f"R2: {r2['objects']:,} objects, {r2['bytes']:,} bytes ({r2['percent']:.2f}%) [{r2['level']}]")
                print(f"R2 scope: {r2['scope']}")
            print("Provider dashboard remains authoritative for full-service storage, request allowances and billing.")
            if database["providerKind"] == "cockroachdb":
                print("Cockroach Request Units must be monitored separately in Cockroach Cloud.")
        return 2 if exceeded else 0
    except Exception as exc:
        # Connection/query exceptions may contain a connection string. Never
        # print their text, and never report incomplete measurements as 0/OK.
        message = str(exc) if isinstance(exc, StorageCheckError) else f"Storage measurement failed ({type(exc).__name__}); usage is unknown."
        print(json.dumps({"status": "error", "message": message}) if args.json else message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
