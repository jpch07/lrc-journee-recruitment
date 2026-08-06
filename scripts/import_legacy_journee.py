from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import initialize_database, session_scope  # noqa: E402
from app.legacy_import import LegacyWorkbookImporter  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate or import a completed legacy Journee workbook.")
    parser.add_argument("--workbook", required=True)
    parser.add_argument("--journey-name", required=True)
    parser.add_argument("--event-date", required=True, type=date.fromisoformat)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    importer = LegacyWorkbookImporter(args.workbook, args.journey_name, args.event_date)
    try:
        if args.confirm:
            initialize_database()
            with session_scope() as db:
                _journey, report = importer.import_into(db)
            report["mode"] = "imported"
        else:
            report = importer.analyze(); report["mode"] = "dry-run"
        print(json.dumps(report, indent=2, ensure_ascii=False)); return 0
    finally:
        importer.close()


if __name__ == "__main__":
    raise SystemExit(main())
