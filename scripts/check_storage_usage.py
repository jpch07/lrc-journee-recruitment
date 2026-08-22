"""Report storage headroom and fail at configured warning thresholds.

This measures application logical row bytes (not Cockroach replication or
compression) and exact R2 object bytes. Cockroach Cloud remains authoritative
for monthly Request Unit consumption; its Basic-cluster Metrics page should
have matching 50%, 75%, and 90% notifications enabled before cutover.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings
from app.db import SessionLocal
from app.object_storage import _client, enabled


COCKROACH_ALLOWANCE = 10 * 1024**3
R2_ALLOWANCE = 10 * 1024**3


def level(percent: float) -> str:
    if percent >= 90:
        return "CRITICAL"
    if percent >= 75:
        return "WARNING"
    if percent >= 50:
        return "NOTICE"
    return "OK"


def database_logical_bytes() -> int:
    total = 0
    with SessionLocal() as db:
        names = [row[0] for row in db.execute(text(
            "select table_name from information_schema.tables "
            "where table_schema='journee_recruitment' and table_type='BASE TABLE'"
        ))]
        for name in names:
            if not name.replace("_", "").isalnum():
                continue
            try:
                total += int(db.execute(text(
                    f'select coalesce(sum(pg_column_size(t)),0) from journee_recruitment."{name}" t'
                )).scalar() or 0)
            except Exception:
                db.rollback()
    return total


def r2_bytes() -> tuple[int, int]:
    if not enabled():
        return 0, 0
    client = _client()
    count = total = 0
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=settings.r2_bucket):
        for item in page.get("Contents", []):
            count += 1
            total += int(item.get("Size", 0))
    return count, total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fail-at", type=float, default=90)
    args = parser.parse_args()
    db_size = database_logical_bytes()
    object_count, object_size = r2_bytes()
    db_percent = db_size / COCKROACH_ALLOWANCE * 100
    r2_percent = object_size / R2_ALLOWANCE * 100
    print(f"Cockroach logical data: {db_size:,} bytes ({db_percent:.2f}%) [{level(db_percent)}]")
    print(f"R2 photos: {object_count:,} objects, {object_size:,} bytes ({r2_percent:.2f}%) [{level(r2_percent)}]")
    print("Request Units: monitor tenant.sql_usage.request_units in Cockroach Cloud (monthly allowance alert required).")
    return 2 if max(db_percent, r2_percent) >= args.fail_at else 0


if __name__ == "__main__":
    raise SystemExit(main())
