"""Backfill recruit birth dates from the cached master recruit directory.

Matching prefers an existing directory link, then a unique normalized full name,
then a unique normalized phone number. Preview is the default; pass ``--confirm``
to write. Existing birth dates are never overwritten.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
import sys
import unicodedata

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Journey, Recruit, RecruitDirectory
from app.utils import audit


DEFAULT_JOURNEYS = (
    "2 August - Journee 2026 AM",
    "2 August - Journee 2026 PM",
)


def normalized_name(value: str | None) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    return "".join(character for character in decomposed.casefold() if character.isalnum())


def normalized_phone(value: str | None) -> str:
    digits = "".join(character for character in (value or "") if character.isdigit())
    return digits[-8:] if len(digits) >= 7 else ""


def unique_index(items: list[RecruitDirectory], key) -> dict[str, RecruitDirectory]:
    grouped: dict[str, list[RecruitDirectory]] = defaultdict(list)
    for item in items:
        value = key(item)
        if value:
            grouped[value].append(item)
    return {value: matches[0] for value, matches in grouped.items() if len(matches) == 1}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journey", action="append", dest="journeys")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    journey_names = tuple(args.journeys or DEFAULT_JOURNEYS)

    with SessionLocal() as db:
        directory = list(db.scalars(select(RecruitDirectory).where(RecruitDirectory.active.is_(True))))
        directory_by_id = {item.id: item for item in directory}
        directory_by_name = unique_index(directory, lambda item: normalized_name(item.name))
        directory_by_phone = unique_index(directory, lambda item: normalized_phone(item.phone_number))
        summaries: list[dict] = []

        for journey_name in journey_names:
            journeys = list(db.scalars(select(Journey).where(Journey.name == journey_name)))
            if len(journeys) != 1:
                raise SystemExit(f"Expected one Journee named {journey_name!r}; found {len(journeys)}.")
            journey = journeys[0]
            recruits = list(db.scalars(select(Recruit).where(
                Recruit.journey_id == journey.id,
                Recruit.active.is_(True),
            )))
            birth_dates_added = 0
            directory_links_added = 0
            matched = 0
            missing_birth_date: list[str] = []

            for recruit in recruits:
                match = directory_by_id.get(recruit.directory_id or "")
                if not match:
                    match = directory_by_name.get(normalized_name(recruit.name))
                if not match:
                    match = directory_by_phone.get(normalized_phone(recruit.phone_number))
                if not match:
                    missing_birth_date.append(recruit.name)
                    continue
                matched += 1
                changed = False
                if not recruit.directory_id:
                    recruit.directory_id = match.id
                    directory_links_added += 1
                    changed = True
                if recruit.date_of_birth is None and match.date_of_birth is not None:
                    recruit.date_of_birth = match.date_of_birth
                    birth_dates_added += 1
                    changed = True
                elif recruit.date_of_birth is None:
                    missing_birth_date.append(recruit.name)
                if changed:
                    recruit.version += 1

            summary = {
                "journey": journey.name,
                "recruits": len(recruits),
                "matched": matched,
                "birthDatesAdded": birth_dates_added,
                "directoryLinksAdded": directory_links_added,
                "missingBirthDates": missing_birth_date,
            }
            summaries.append(summary)
            if args.confirm:
                audit(
                    db,
                    journey_id=journey.id,
                    actor_type="maintenance",
                    actor_name="Recruit directory birth-date backfill",
                    action="recruits.birth_dates_backfilled",
                    entity_type="journey",
                    entity_id=journey.id,
                    after={
                        "recruits": len(recruits),
                        "matched": matched,
                        "birthDatesAdded": birth_dates_added,
                        "directoryLinksAdded": directory_links_added,
                        "missingBirthDateCount": len(missing_birth_date),
                    },
                )

        for summary in summaries:
            print(
                f"{summary['journey']}: {summary['birthDatesAdded']} birth dates would be added; "
                f"{len(summary['missingBirthDates'])} remain unavailable in the master directory."
            )
            if summary["missingBirthDates"]:
                print("  Missing in source: " + ", ".join(summary["missingBirthDates"]))
        if not args.confirm:
            print("Preview only. Re-run with --confirm to write changes.")
            db.rollback()
            return
        db.commit()
        print("Birth-date backfill completed successfully.")


if __name__ == "__main__":
    main()
