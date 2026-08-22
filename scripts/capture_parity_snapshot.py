"""Create a deterministic, credential-free parity manifest for a database.

Run once against Neon and once against CockroachDB. Matching manifest hashes
prove that every non-runtime relational row (including scores, permissions,
audits, photo metadata, and inline-photo checksums) was copied exactly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import Base, engine
from app import models  # noqa: F401


SKIP = {"admin_sessions", "evaluator_sessions", "user_sessions", "platform_sessions", "idempotency_records"}


def normalized(value):
    if isinstance(value, bytes):
        return {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    manifest = {}
    with engine.connect() as connection:
        for table in Base.metadata.sorted_tables:
            if table.name in SKIP:
                continue
            rows = []
            for row in connection.execute(select(table)).mappings():
                rows.append({key: normalized(value) for key, value in sorted(row.items())})
            rows.sort(key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":")))
            encoded = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
            manifest[table.name] = {"rows": len(rows), "sha256": hashlib.sha256(encoded).hexdigest()}
    overall = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    payload = {"overallSha256": overall, "tables": manifest}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Parity snapshot: {overall} ({args.output})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
