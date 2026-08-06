from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.config import settings
from app.db import initialize_database, session_scope
from app.models import Evaluator, Journey, MandatoryRoomEvaluator, Recruit
from app.services import (
    create_assignment_preview,
    create_journey,
    create_room_preview,
    publish_assignment_round,
    publish_room_plan,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a fictional Journee for local demonstrations.")
    parser.add_argument("--confirm", action="store_true", help="Required acknowledgement that records will be inserted.")
    args = parser.parse_args()
    if not args.confirm:
        raise SystemExit("Re-run with --confirm to insert the fictional demo records.")
    if settings.is_production:
        raise SystemExit("Demo seeding is disabled when LRC_JOURNEE_ENV=production.")

    initialize_database()
    with session_scope() as db:
        existing = db.scalar(select(Journey).where(Journey.name == "Fictional Demo Journee"))
        if existing:
            print(f"Demo already exists: {existing.id}")
            return
        journey = create_journey(db, "Fictional Demo Journee", date.today(), 3, "Demo Seeder")
        recruits = []
        evaluators = []
        for index in range(1, 13):
            recruit = Recruit(
                journey_id=journey.id,
                phone_number=f"7010{index:04d}",
                name=f"Fictional Recruit {index:02d}",
                present=True,
                arrival_time=datetime.now(timezone.utc),
            )
            db.add(recruit)
            recruits.append(recruit)
            evaluator = Evaluator(
                journey_id=journey.id,
                name=f"Fictional Evaluator {index:02d}",
                role="overall" if index <= 7 else "dossard",
                present=True,
            )
            db.add(evaluator)
            evaluators.append(evaluator)
        db.flush()
        for room, evaluator in enumerate(evaluators[:3], start=1):
            db.add(MandatoryRoomEvaluator(journey_id=journey.id, evaluator_id=evaluator.id, room_number=room))

        room_plan = create_room_preview(db, journey, "Demo Seeder", "demo-rooms")
        publish_room_plan(db, journey, room_plan, "Demo Seeder")
        for code in ("sport", "escape_room", "negotiation", "skills", "simulation"):
            assignment_round = create_assignment_preview(db, journey, code, "Demo Seeder", f"demo-{code}")
            publish_assignment_round(db, journey, assignment_round, "Demo Seeder")
        print(f"Created demo Journee: {journey.id}")


if __name__ == "__main__":
    main()
