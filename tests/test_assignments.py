from __future__ import annotations

from collections import Counter
from datetime import date

from sqlalchemy import select

from app.assignments import EvaluatorCandidate, RecruitCandidate, generate_pairings, generate_room_plan
from app.db import session_scope
from app.models import ActivityState, Assignment, AssignmentRound, Evaluator, Recruit
from app.services import (
    create_assignment_preview,
    create_journey,
    create_room_preview,
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


def test_publishing_skills_automatically_publishes_same_simulation_pairing():
    with session_scope() as db:
        journey = create_journey(db, "Simulation Copy Test", date(2026, 10, 1), 2, "Test")
        for index in range(4):
            db.add(Recruit(journey_id=journey.id, name=f"Recruit {index}", present=True))
            db.add(Evaluator(journey_id=journey.id, name=f"Evaluator {index}", role="overall", present=True))
        db.flush()
        room_plan = create_room_preview(db, journey, "Test", "rooms")
        publish_room_plan(db, journey, room_plan, "Test")
        skills = create_assignment_preview(db, journey, "skills", "Test", "skills")
        db.flush()
        skill_assignments = list(db.scalars(select(Assignment).where(Assignment.round_id == skills.id)))
        assert skill_assignments
        assert all(item.room_number is None for item in skill_assignments)
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
