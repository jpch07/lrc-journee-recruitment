from __future__ import annotations

from collections import Counter
from datetime import date

from sqlalchemy import select

from app.assignments import (EvaluatorCandidate, RecruitCandidate, distribute_evaluators_to_rooms,
                             generate_pairings, generate_room_plan)
from app.db import session_scope
from app.models import (ActivityEvaluatorAvailability, ActivityMandatoryEvaluator, ActivityState,
                        Assignment, AssignmentRound, Evaluator, Recruit, RoomPlanEvaluator,
                        RoomPlanRecruit)
from app.services import (
    create_activity_room_preview,
    create_assignment_preview,
    create_journey,
    create_room_preview,
    ensure_activity_operation,
    publish_assignment_round,
    publish_room_plan,
)


def recruits(count, room=None):
    return [RecruitCandidate(f"r{index}", f"Recruit {index}", room) for index in range(count)]


def evaluators(count, room=None, overall=0):
    return [EvaluatorCandidate(f"e{index}", f"Evaluator {index}", "overall" if index < overall else "dossard", room) for index in range(count)]


def test_room_plan_balances_recruits_and_preserves_mandatory():
    result = generate_room_plan(recruits(17), evaluators(12, overall=5), {"e0": 3, "e4": 1}, 4, "fixed")
    sizes = Counter(result.recruit_rooms.values())
    assert max(sizes.values()) - min(sizes.values()) <= 1
    assert result.evaluator_rooms["e0"] == 3
    assert result.evaluator_rooms["e4"] == 1


def test_evaluator_only_distribution_preserves_manual_locks():
    rooms, _mandatory, _warnings = distribute_evaluators_to_rooms(
        {"r1": 1, "r2": 1, "r3": 2, "r4": 2},
        evaluators(4, overall=2), {}, 2, "locked", locked_rooms={"e0": 2},
    )
    assert rooms["e0"] == 2
    assert set(rooms) == {"e0", "e1", "e2", "e3"}


def test_seed_reproduces_room_and_pairing_preview():
    first = generate_room_plan(recruits(12), evaluators(10, overall=4), {}, 3, "same-seed")
    second = generate_room_plan(recruits(12), evaluators(10, overall=4), {}, 3, "same-seed")
    assert first.recruit_rooms == second.recruit_rooms
    pairing1 = generate_pairings(recruits(8), evaluators(8, overall=4), room_based=False, seed="same")
    pairing2 = generate_pairings(recruits(8), evaluators(8, overall=4), room_based=False, seed="same")
    assert pairing1.assignments == pairing2.assignments


def test_primary_before_secondary_and_maximum_two():
    result = generate_pairings(recruits(5), evaluators(9, overall=5), room_based=False, seed="coverage")
    slots = Counter(item.recruit_id for item in result.assignments)
    assert set(slots) == {f"r{index}" for index in range(5)}
    assert max(slots.values()) <= 2
    assert sum(item.slot == 1 for item in result.assignments) == 5


def test_dossards_have_priority_for_secondary_slots():
    result = generate_pairings(recruits(2), evaluators(4, overall=2), room_based=False, seed="dossard-secondary")
    secondary_ids = {item.evaluator_id for item in result.assignments if item.slot == 2}
    assert secondary_ids == {"e2", "e3"}


def test_shortage_balances_loads_and_has_no_secondaries():
    result = generate_pairings(recruits(11), evaluators(4, overall=2), room_based=False, seed="shortage")
    loads = Counter(item.evaluator_id for item in result.assignments)
    assert len(result.assignments) == 11
    assert max(loads.values()) - min(loads.values()) <= 1
    assert all(item.slot == 1 for item in result.assignments)


def test_room_activity_never_crosses_rooms_and_avoids_repeats():
    room_recruits = recruits(3, 1) + [RecruitCandidate(f"x{index}", f"X {index}", 2) for index in range(3)]
    room_evaluators = evaluators(3, 1, overall=2) + [EvaluatorCandidate(f"x{index}", f"XE {index}", "overall", 2) for index in range(3)]
    past = {("e0", "r0"), ("x0", "x0")}
    result = generate_pairings(room_recruits, room_evaluators, room_based=True, past_pairs=past, seed="rooms")
    recruit_rooms = {item.id: item.room_number for item in room_recruits}
    evaluator_rooms = {item.id: item.room_number for item in room_evaluators}
    assert all(recruit_rooms[item.recruit_id] == evaluator_rooms[item.evaluator_id] for item in result.assignments)
    assert not any((item.evaluator_id, item.recruit_id) in past for item in result.assignments)


def test_activity_availability_and_room_plans_are_independent_and_copy_recruits():
    with session_scope() as db:
        journey = create_journey(db, "Independent Activity Plans", date(2026, 10, 2), 2, "Test")
        for index in range(6):
            db.add(Recruit(journey_id=journey.id, name=f"Recruit {index}", present=True))
            db.add(Evaluator(journey_id=journey.id, name=f"Evaluator {index}", role="overall" if index < 3 else "dossard", present=True))
        db.flush()
        sport_operation = ensure_activity_operation(db, journey, "sport")
        sport_availability = list(db.scalars(select(ActivityEvaluatorAvailability).where(
            ActivityEvaluatorAvailability.journey_id == journey.id,
            ActivityEvaluatorAvailability.activity_code == "sport",
        )))
        sport_availability[0].available = False
        escape_operation = ensure_activity_operation(db, journey, "escape_room")
        negotiation_operation = ensure_activity_operation(db, journey, "negotiation")
        assert sport_operation.initialized_from is None
        assert escape_operation.initialized_from == "sport"
        assert negotiation_operation.initialized_from == "escape_room"
        escape_availability = list(db.scalars(select(ActivityEvaluatorAvailability).where(
            ActivityEvaluatorAvailability.journey_id == journey.id,
            ActivityEvaluatorAvailability.activity_code == "escape_room",
        )))
        assert not next(item.available for item in escape_availability if item.evaluator_id == sport_availability[0].evaluator_id)
        negotiation_availability = list(db.scalars(select(ActivityEvaluatorAvailability).where(
            ActivityEvaluatorAvailability.journey_id == journey.id,
            ActivityEvaluatorAvailability.activity_code == "negotiation",
        )))
        escape_values_before = dict(db.execute(select(
            ActivityEvaluatorAvailability.evaluator_id, ActivityEvaluatorAvailability.available,
        ).where(
            ActivityEvaluatorAvailability.journey_id == journey.id,
            ActivityEvaluatorAvailability.activity_code == "escape_room",
        )).all())
        next(item for item in negotiation_availability if item.available).available = False
        escape_values_after = dict(db.execute(select(
            ActivityEvaluatorAvailability.evaluator_id, ActivityEvaluatorAvailability.available,
        ).where(
            ActivityEvaluatorAvailability.journey_id == journey.id,
            ActivityEvaluatorAvailability.activity_code == "escape_room",
        )).all())
        assert escape_values_after == escape_values_before
        assert escape_operation.id != negotiation_operation.id

        late = Evaluator(journey_id=journey.id, name="Late Evaluator", role="dossard", present=True)
        db.add(late); db.flush()
        ensure_activity_operation(db, journey, "negotiation")
        db.flush()
        late_values = dict(db.execute(select(
            ActivityEvaluatorAvailability.activity_code, ActivityEvaluatorAvailability.available,
        ).where(ActivityEvaluatorAvailability.evaluator_id == late.id)).all())
        assert late_values == {"sport": True, "escape_room": True, "negotiation": True}

        escape = create_room_preview(db, journey, "Test", "escape-seed", "escape_room")
        publish_room_plan(db, journey, escape, "Test")
        negotiation = create_activity_room_preview(db, journey, "negotiation", "Test", "nego-seed", "copy_recruits")
        db.flush()
        escape_rooms = dict(db.execute(select(RoomPlanRecruit.recruit_id, RoomPlanRecruit.room_number).where(
            RoomPlanRecruit.plan_id == escape.id)).all())
        negotiation_rooms = dict(db.execute(select(RoomPlanRecruit.recruit_id, RoomPlanRecruit.room_number).where(
            RoomPlanRecruit.plan_id == negotiation.id)).all())
        assert negotiation.activity_code == "negotiation"
        assert negotiation_rooms == escape_rooms


def test_copy_full_plan_keeps_current_availability_and_removes_unavailable_people():
    with session_scope() as db:
        journey = create_journey(db, "Copy Complete Plan", date(2026, 10, 3), 2, "Test")
        recruit_rows = [Recruit(journey_id=journey.id, name=f"Recruit {index}", present=True) for index in range(4)]
        evaluator_rows = [Evaluator(journey_id=journey.id, name=f"Evaluator {index}", role="overall", present=True) for index in range(4)]
        db.add_all([*recruit_rows, *evaluator_rows]); db.flush()
        escape = create_room_preview(db, journey, "Test", "escape", "escape_room")
        db.flush()
        source_members = list(db.scalars(select(RoomPlanEvaluator).where(RoomPlanEvaluator.plan_id == escape.id)))
        source_members[0].locked = True
        source_members[1].mandatory = True
        source_members[2].mandatory = True
        db.add(ActivityMandatoryEvaluator(journey_id=journey.id, activity_code="escape_room",
                                          evaluator_id=source_members[1].evaluator_id,
                                          room_number=source_members[1].room_number))
        db.add(ActivityMandatoryEvaluator(journey_id=journey.id, activity_code="escape_room",
                                          evaluator_id=source_members[2].evaluator_id,
                                          room_number=source_members[2].room_number))
        db.flush()
        publish_room_plan(db, journey, escape, "Test")
        ensure_activity_operation(db, journey, "negotiation")
        unavailable_id = source_members[2].evaluator_id
        unavailable = db.scalar(select(ActivityEvaluatorAvailability).where(
            ActivityEvaluatorAvailability.journey_id == journey.id,
            ActivityEvaluatorAvailability.activity_code == "negotiation",
            ActivityEvaluatorAvailability.evaluator_id == unavailable_id,
        ))
        unavailable.available = False
        copied = create_activity_room_preview(db, journey, "negotiation", "Test", "copy", "copy_full")
        db.flush()
        copied_members = list(db.scalars(select(RoomPlanEvaluator).where(RoomPlanEvaluator.plan_id == copied.id)))
        copied_by_id = {item.evaluator_id: item for item in copied_members}
        assert unavailable_id not in copied_by_id
        assert copied_by_id[source_members[0].evaluator_id].locked
        target_mandatory = {item.evaluator_id for item in db.scalars(select(ActivityMandatoryEvaluator).where(
            ActivityMandatoryEvaluator.journey_id == journey.id,
            ActivityMandatoryEvaluator.activity_code == "negotiation",
        ))}
        assert target_mandatory == {source_members[1].evaluator_id}


def test_publishing_skills_automatically_publishes_same_simulation_pairing():
    with session_scope() as db:
        journey = create_journey(db, "Simulation Copy Test", date(2026, 10, 1), 2, "Test")
        for index in range(4):
            db.add(Recruit(journey_id=journey.id, name=f"Recruit {index}", present=True))
            db.add(Evaluator(journey_id=journey.id, name=f"Evaluator {index}", role="overall", present=True))
        db.flush()
        room_plan = create_room_preview(db, journey, "Test", "rooms", "escape_room")
        publish_room_plan(db, journey, room_plan, "Test")
        negotiation_rooms = create_activity_room_preview(db, journey, "negotiation", "Test", "nego-rooms", "copy_recruits")
        publish_room_plan(db, journey, negotiation_rooms, "Test")
        skills_rooms = create_activity_room_preview(db, journey, "skills", "Test", "skills-rooms", "copy_recruits")
        publish_room_plan(db, journey, skills_rooms, "Test")
        skills = create_assignment_preview(db, journey, "skills", "Test", "skills")
        db.flush()
        skill_assignments = list(db.scalars(select(Assignment).where(Assignment.round_id == skills.id)))
        assert skill_assignments
        assert all(item.room_number is not None for item in skill_assignments)
        publish_assignment_round(db, journey, skills, "Test")
        db.flush()
        simulation_state = db.scalar(select(ActivityState).where(
            ActivityState.journey_id == journey.id, ActivityState.code == "simulation"
        ))
        simulation = db.get(AssignmentRound, simulation_state.assignment_round_id)
        skills_pairs = set(
            db.execute(
                select(Assignment.evaluator_id, Assignment.recruit_id, Assignment.room_number, Assignment.slot)
                .where(Assignment.round_id == skills.id)
            ).all()
        )
        simulation_pairs = set(
            db.execute(
                select(Assignment.evaluator_id, Assignment.recruit_id, Assignment.room_number, Assignment.slot)
                .where(Assignment.round_id == simulation.id)
            ).all()
        )
        assert simulation.reused_from_id == skills.id
        assert simulation.status == "published"
        assert simulation_pairs == skills_pairs
        initial_skill_keys = {(item.evaluator_id, item.recruit_id): item.task_key for item in db.scalars(
            select(Assignment).where(Assignment.round_id == skills.id))}
        initial_simulation_keys = {(item.evaluator_id, item.recruit_id): item.task_key for item in db.scalars(
            select(Assignment).where(Assignment.round_id == simulation.id))}
        replacement = create_assignment_preview(db, journey, "skills", "Test", "skills")
        db.flush()
        replacement_keys = {(item.evaluator_id, item.recruit_id): item.task_key for item in db.scalars(
            select(Assignment).where(Assignment.round_id == replacement.id))}
        assert replacement_keys == initial_skill_keys
        publish_assignment_round(db, journey, replacement, "Test")
        db.flush()
        replacement_simulation = db.get(AssignmentRound, simulation_state.assignment_round_id)
        replacement_simulation_keys = {
            (item.evaluator_id, item.recruit_id): item.task_key for item in db.scalars(
                select(Assignment).where(Assignment.round_id == replacement_simulation.id))
        }
        assert replacement_simulation_keys == initial_simulation_keys
