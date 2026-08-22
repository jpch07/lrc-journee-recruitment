from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from contextvars import ContextVar
from collections.abc import Iterator, Mapping, Sequence, Set
from typing import Any


_LRC_ACTIVITY_ORDER = ["sport", "escape_room", "negotiation", "skills", "simulation"]
# Escape Room and Negotiation are constrained by the published room plan.
# Skills is generated globally after Negotiation, and Simulation copies Skills.
_LRC_ROOM_ACTIVITIES = {"escape_room", "negotiation", "skills", "simulation"}
_LRC_BEHAVIORAL_DIMENSIONS = ["willingness", "adaptability", "respect", "intelligence"]
_LRC_DIMENSION_ORDER = [*_LRC_BEHAVIORAL_DIMENSIONS, "application", "physical_ability"]
_LRC_DIMENSION_NAMES = {
    "willingness": "Willingness",
    "respect": "Respect",
    "adaptability": "Adaptability",
    "intelligence": "Intelligence",
    "application": "Application",
    "physical_ability": "Physical Ability",
}
_LRC_DIMENSION_ACTIVITIES = ["escape_room", "negotiation", "simulation"]
ONE_SIXTH = Decimal("1") / Decimal("6")


@dataclass(frozen=True)
class Criterion:
    key: str
    dimension: str
    name: str
    explanation: str
    weight: Decimal
    input_type: str = "grade"
    target: Decimal | None = None
    unit: str = ""


@dataclass(frozen=True)
class ActivityRubric:
    code: str
    name: str
    criteria: tuple[Criterion, ...]
    kind: str = "weighted"


_LRC_RUBRICS: dict[str, ActivityRubric] = {
    "sport": ActivityRubric(
        code="sport",
        name="Sport",
        kind="sport",
        criteria=(
            Criterion("push_ups", "Endurance", "Push Ups", "Enter the completed number of push-ups.", Decimal("0.25"), "integer", Decimal("30"), "repetitions"),
            Criterion("wall_sit", "Endurance", "Wall Sit", "Enter the wall-sit duration.", Decimal("0.25"), "duration", Decimal("120"), "seconds"),
            Criterion("beep_test", "Endurance", "Beep Test", "Enter the beep-test duration.", Decimal("0.25"), "duration", Decimal("600"), "seconds"),
            Criterion("plank", "Endurance", "Plank", "Enter the plank duration.", Decimal("0.25"), "duration", Decimal("120"), "seconds"),
        ),
    ),
    "escape_room": ActivityRubric(
        code="escape_room",
        name="Escape Room",
        criteria=(
            Criterion("active_participation", "Willingness", "Active participation and contribution", "Engaging continuously with the team during puzzle solving rather than withdrawing", Decimal("0.20")),
            Criterion("communication_active_listening", "Respect", "Communication and active listening", "Sharing clues clearly, listening to team ideas, and avoiding talking over others", ONE_SIXTH),
            Criterion("self_control_composure", "Respect", "Self-control and composure", "Staying calm, supportive, and focused despite time limits and locked-room pressure", ONE_SIXTH),
            Criterion("time_management", "Adaptability", "Time management under pressure", "Maintaining steady focus and momentum without freezing or panicking as time ticks down", ONE_SIXTH),
            Criterion("setback_recovery", "Adaptability", "Setback recovery and resourcefulness", "Ability to pivot strategies smoothly when a proposed puzzle answer fails or when stuck", ONE_SIXTH),
            Criterion("logical_problem_solving", "Intelligence", "Logical problem solving and pattern recognition", "Efficiency in analyzing supplied clues", ONE_SIXTH),
            Criterion("common_sense_prioritization", "Intelligence", "Common sense prioritization", "Using common sense to order priorities by severity", ONE_SIXTH),
        ),
    ),
    "negotiation": ActivityRubric(
        code="negotiation",
        name="Negotiation",
        criteria=(
            Criterion("initiative_persistence", "Willingness", "Initiative and persistence", "Staying engaged and trying supportive approaches through difficult or resistant responses", Decimal("0.20")),
            Criterion("team_support", "Willingness", "Team support and coordination", "Backing up teammates without interrupting or sending conflicting messages", Decimal("0.20")),
            Criterion("empathy_listening", "Respect", "Empathy and active listening", "Validating concerns with warmth rather than clinical force", ONE_SIXTH),
            Criterion("non_aggressive_communication", "Respect", "Non-aggressive communication", "Maintaining a calm tone and avoiding hostile or pushy language", ONE_SIXTH),
            Criterion("adaptable_behavior", "Adaptability", "Adaptable to dynamic patient behavior", "Pivoting smoothly when patients change their objections or become emotionally heightened", ONE_SIXTH),
            Criterion("self_preservation", "Adaptability", "Self-preservation and safety awareness", "Recognizing safety boundaries during high-risk escalations", ONE_SIXTH),
            Criterion("reasoning_persuasion", "Intelligence", "Clarity of reasoning and persuasion", "Offering simple, reassuring options", ONE_SIXTH),
            Criterion("understanding_boundaries", "Intelligence", "Understanding boundaries", "Knowing when to negotiate and when to pause for safety and seek help", ONE_SIXTH),
        ),
    ),
    "skills": ActivityRubric(
        code="skills",
        name="Skills",
        criteria=(
            Criterion("posture_lifting", "Brancardage", "Posture and lifting technique", "Correct body posture and safe lifting technique", Decimal("0.15")),
            Criterion("physical_capability", "Brancardage", "Physical capability and carrying ability", "Ability to carry and maneuver the stretcher safely", Decimal("0.15")),
            Criterion("team_synchronization", "Brancardage", "Team synchronization and command timing", "Coordinating movements and following commands at the correct time", Decimal("0.10")),
            Criterion("communication_encouragement", "Brancardage", "Communication and encouragement", "Communicating clearly and encouraging teammates", Decimal("0.10")),
            Criterion("grasping_concepts", "Brancardage", "Grasping of concepts", "Understanding and applying the concepts taught", Decimal("0.10")),
            Criterion("correct_placement", "Bandage", "Correct placement and coverage", "Placing the bandage correctly and covering the required area", Decimal("0.10")),
            Criterion("tension_stability", "Bandage", "Tension and stability", "Maintaining suitable bandage tension and stability", Decimal("0.10")),
            Criterion("efficiency_speed", "Bandage", "Efficiency and speed", "Completing the technique efficiently without sacrificing accuracy", Decimal("0.10")),
            Criterion("composure_precision", "Bandage", "Composure and precision", "Remaining calm and precise while applying the bandage", Decimal("0.10")),
        ),
    ),
    "simulation": ActivityRubric(
        code="simulation",
        name="Simulation",
        criteria=(
            Criterion("commitment_effort", "Willingness", "Commitment and effort under difficulty", "Sustaining physical effort, grip, and enthusiasm throughout the simulation", Decimal("0.20")),
            Criterion("team_cohesion", "Willingness", "Team cohesion and synchronization", "Coordinating movements during lifting, carrying, and patient placement", Decimal("0.20")),
            Criterion("composure_distraction", "Respect", "Respect under pressure", "Maintaining respect and calm execution despite pressurizing elements", ONE_SIXTH),
            Criterion("patient_empathy", "Respect", "Patient empathy and reassurance", "Providing compassionate communication during care and transport", ONE_SIXTH),
            Criterion("bystander_management", "Adaptability", "Bystander/distraction management", "Handling distracting elements without losing focus on patient care", ONE_SIXTH),
            Criterion("recovery_adjustment", "Adaptability", "Recovery and dynamic adjustment", "Adjusting smoothly when obstacles or sudden scenario changes occur", ONE_SIXTH),
            Criterion("retention_skills", "Intelligence", "Retention and application of taught skills", "Applying bandage and stretcher techniques accurately", ONE_SIXTH),
            Criterion("role_assignment", "Intelligence", "Role assignment and task clarity", "Assigning clear roles before taking action", ONE_SIXTH),
        ),
    ),
}


_runtime_data: ContextVar[dict[str, Any] | None] = ContextVar("assessment_rubric_runtime", default=None)
_defaults = {
    "activity_order": _LRC_ACTIVITY_ORDER,
    "room_activities": _LRC_ROOM_ACTIVITIES,
    "behavioral_dimensions": _LRC_BEHAVIORAL_DIMENSIONS,
    "dimension_order": _LRC_DIMENSION_ORDER,
    "dimension_names": _LRC_DIMENSION_NAMES,
    "dimension_activities": _LRC_DIMENSION_ACTIVITIES,
    "rubrics": _LRC_RUBRICS,
}


def set_runtime_data(data: dict[str, Any]):
    return _runtime_data.set(data)


def reset_runtime_data(token) -> None:
    _runtime_data.reset(token)


def _runtime_value(key: str):
    return (_runtime_data.get() or _defaults)[key]


class _SequenceProxy(Sequence):
    def __init__(self, key: str): self.key = key
    def __getitem__(self, index): return _runtime_value(self.key)[index]
    def __len__(self) -> int: return len(_runtime_value(self.key))
    def __iter__(self) -> Iterator: return iter(_runtime_value(self.key))
    def __contains__(self, value) -> bool: return value in _runtime_value(self.key)
    def index(self, value, *args) -> int: return _runtime_value(self.key).index(value, *args)


class _SetProxy(Set):
    def __init__(self, key: str): self.key = key
    def __contains__(self, value) -> bool: return value in _runtime_value(self.key)
    def __len__(self) -> int: return len(_runtime_value(self.key))
    def __iter__(self) -> Iterator: return iter(_runtime_value(self.key))


class _MappingProxy(Mapping):
    def __init__(self, key: str): self.key = key
    def __getitem__(self, item): return _runtime_value(self.key)[item]
    def __len__(self) -> int: return len(_runtime_value(self.key))
    def __iter__(self) -> Iterator: return iter(_runtime_value(self.key))
    def get(self, item, default=None): return _runtime_value(self.key).get(item, default)
    def values(self): return _runtime_value(self.key).values()
    def items(self): return _runtime_value(self.key).items()


ACTIVITY_ORDER = _SequenceProxy("activity_order")
ROOM_ACTIVITIES = _SetProxy("room_activities")
BEHAVIORAL_DIMENSIONS = _SequenceProxy("behavioral_dimensions")
DIMENSION_ORDER = _SequenceProxy("dimension_order")
DIMENSION_NAMES = _MappingProxy("dimension_names")
DIMENSION_ACTIVITIES = _SequenceProxy("dimension_activities")
RUBRICS = _MappingProxy("rubrics")


def public_rubric(activity_code: str) -> dict:
    from .assessment_runtime import activity_definition
    rubric = RUBRICS[activity_code]
    configured = activity_definition(activity_code)
    configured_by_key = {item.key: item for item in configured.criteria} if configured else {}
    return {
        "code": rubric.code,
        "name": rubric.name,
        "kind": rubric.kind,
        "commentsEnabled": configured.commentsEnabled if configured else True,
        "criteria": [
            {
                "key": item.key,
                "dimension": item.dimension,
                "name": item.name,
                "explanation": item.explanation,
                "weight": float(item.weight),
                "inputType": item.input_type,
                "target": float(item.target) if item.target is not None else None,
                "unit": item.unit,
                "minimum": float(configured_by_key[item.key].minimum) if item.key in configured_by_key else 0,
                "maximum": float(configured_by_key[item.key].maximum) if item.key in configured_by_key else 5,
                "step": float(configured_by_key[item.key].step) if item.key in configured_by_key else 0.1,
            }
            for item in rubric.criteria
        ],
    }


def evaluator_rubric(activity_code: str) -> dict:
    """Expose only form-building metadata; scoring weights and Sport targets stay private."""
    from .assessment_runtime import activity_definition
    rubric = RUBRICS[activity_code]
    configured = activity_definition(activity_code)
    configured_by_key = {item.key: item for item in configured.criteria} if configured else {}
    return {
        "code": rubric.code,
        "name": rubric.name,
        "kind": rubric.kind,
        "commentsEnabled": configured.commentsEnabled if configured else True,
        "criteria": [
            {
                "key": item.key,
                "dimension": item.dimension if not configured or configured.evaluatorShowsDimensions else "",
                "name": item.name,
                "explanation": item.explanation if not configured or configured.evaluatorShowsDescriptions else "",
                "inputType": item.input_type,
                "unit": item.unit,
                "minimum": float(configured_by_key[item.key].minimum) if item.key in configured_by_key else 0,
                "maximum": float(configured_by_key[item.key].maximum) if item.key in configured_by_key else 5,
                "step": float(configured_by_key[item.key].step) if item.key in configured_by_key else 0.1,
            }
            for item in rubric.criteria
        ],
    }


def validate_rubrics() -> None:
    for code in ("sport", "skills"):
        total = sum((criterion.weight for criterion in RUBRICS[code].criteria), Decimal("0"))
        if total != Decimal("1.00"):
            raise RuntimeError(f"{RUBRICS[code].name} weights total {total}, expected 1.00")
    for dimension in BEHAVIORAL_DIMENSIONS:
        total = sum(
            criterion.weight
            for code in DIMENSION_ACTIVITIES
            for criterion in RUBRICS[code].criteria
            if criterion.dimension.casefold() == dimension
        )
        if abs(total - Decimal("1")) > Decimal("0.000000000000000000000001"):
            raise RuntimeError(f"{DIMENSION_NAMES[dimension]} dimension weights total {total}, expected 1.00")
