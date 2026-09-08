from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.assessment_config import AssessmentSystemDefinition, blank_assessment_definition, lrc_assessment_definition
from app.assessment_runtime import activate_assessment_definition, reset_assessment_definition
from app.assessment_service import ensure_assessment_system, publish_definition, structural_signature
from app.db import SessionLocal
from app.models import ActivityState, Assignment, AssignmentRound, Evaluator, Journey, Recruit
from app.rubric import RUBRICS
from app.scoring import target_activity_score
from app.services import create_journey, result_snapshot, save_submission


def _owner_login(client):
    response = client.post("/api/auth/login", json={"username": "JP Chaaya", "password": "test-password"})
    assert response.status_code == 200, response.text
    return response.json()


def test_lrc_preset_exactly_preserves_the_finalized_system():
    definition = lrc_assessment_definition()
    assert [item.key for item in definition.activities] == [
        "sport", "escape_room", "negotiation", "skills", "simulation"
    ]
    assert [item.key for item in definition.assessors.categories] == ["overall", "dossard"]
    assert definition.activities[4].assignment.reuseAssignmentsFrom == "skills"
    assert definition.activities[3].assignment.parallelGroup == "skills_simulation"
    assert definition.activities[4].assignment.parallelGroup == "skills_simulation"
    assert definition.scoring.officialMaximum == Decimal("20")
    assert sum((item.weight for item in definition.scoring.components), Decimal("0")) == Decimal("1")
    assert definition.participants.directorySheetName == "List of Recruits"
    assert definition.assessors.directorySheetName == "Evaluators"


def test_blank_preset_uses_neutral_directory_sheet_names():
    definition = blank_assessment_definition()

    assert definition.participants.directorySheetName == "Participants"
    assert definition.assessors.directorySheetName == "Assessors"


def test_owner_can_publish_a_cosmetic_configuration_without_changing_saved_sessions(client):
    owner = _owner_login(client)
    headers = {"X-CSRF-Token": owner["csrfToken"]}
    journey = client.post("/api/admin/journeys", headers=headers,
                          json={"name": "Compatibility", "event_date": "2026-09-01"})
    assert journey.status_code == 200, journey.text
    payload = client.get("/api/configurator").json()
    definition = deepcopy(payload["draft"])
    definition["branding"]["primaryColor"] = "#315be8"
    published = client.post("/api/configurator/publish", headers=headers, json={
        "definition": definition,
        "base_version": payload["system"]["version"],
        "change_summary": "Cosmetic compatibility test",
    })
    assert published.status_code == 200, published.text
    public = client.get("/api/configurator/public").json()
    assert public["branding"]["primaryColor"] == "#315be8"
    assert [item["key"] for item in public["activities"]] == [
        "sport", "escape_room", "negotiation", "skills", "simulation"
    ]


def test_structural_publish_is_blocked_when_sessions_exist(client):
    owner = _owner_login(client)
    headers = {"X-CSRF-Token": owner["csrfToken"]}
    client.post("/api/admin/journeys", headers=headers,
                json={"name": "Protected history", "event_date": "2026-09-02"})
    payload = client.get("/api/configurator").json()
    definition = deepcopy(payload["draft"])
    definition["activities"][0]["assignment"]["maximumAssessors"] = 1
    response = client.post("/api/configurator/publish", headers=headers, json={
        "definition": definition,
        "base_version": payload["system"]["version"],
        "change_summary": "Must be protected",
    })
    assert response.status_code == 409
    assert "saved Sessions exist" in response.json()["detail"]


@pytest.mark.parametrize("change", [
    "general_maximum",
    "general_step",
    "assessor_aggregation",
    "missing_components",
    "ranking",
    "band_minimum",
    "band_color",
])
def test_structural_signature_tracks_score_rank_and_band_changes(change):
    original = lrc_assessment_definition()
    candidate = lrc_assessment_definition()
    if change == "general_maximum":
        candidate.generalFactors[0].maximum = Decimal("2")
    elif change == "general_step":
        candidate.generalFactors[0].step = Decimal("0.2")
    elif change == "assessor_aggregation":
        candidate.scoring.assessorAggregation = "missing_as_zero"
    elif change == "missing_components":
        candidate.scoring.missingComponents = "exclude"
    elif change == "ranking":
        candidate.scoring.ranking = "dense"
    elif change == "band_minimum":
        candidate.scoring.bands[1].minimum = Decimal("12")
    else:
        candidate.scoring.bands[1].color = "#f59e0b"

    assert structural_signature(candidate) != structural_signature(original)


def test_general_factor_scale_change_is_blocked_when_sessions_exist(client):
    owner = _owner_login(client)
    headers = {"X-CSRF-Token": owner["csrfToken"]}
    created = client.post(
        "/api/admin/journeys",
        headers=headers,
        json={"name": "Protected general scores", "event_date": "2026-09-03"},
    )
    assert created.status_code == 200, created.text
    payload = client.get("/api/configurator").json()
    definition = deepcopy(payload["draft"])
    definition["generalFactors"][0]["maximum"] = "2"
    response = client.post("/api/configurator/publish", headers=headers, json={
        "definition": definition,
        "base_version": payload["system"]["version"],
        "change_summary": "Would recalculate saved general scores",
    })
    assert response.status_code == 409
    assert "saved Sessions exist" in response.json()["detail"]


def test_general_assessment_cannot_be_hidden_while_factors_still_score():
    payload = lrc_assessment_definition().model_dump(mode="json")
    payload["features"]["generalAssessment"] = False

    with pytest.raises(ValidationError, match="cannot be disabled while general factors are configured"):
        AssessmentSystemDefinition.model_validate(payload)


def test_blank_preset_drives_the_existing_session_runtime():
    lrc = lrc_assessment_definition()
    try:
        with SessionLocal() as db:
            system = ensure_assessment_system(db)
            blank = blank_assessment_definition()
            publish_definition(
                db, system, blank, base_version=system.version,
                actor_name="Test owner", change_summary="Blank local system",
            )
            journey = create_journey(db, "Generic session", date(2026, 9, 3), 1, "Test owner")
            db.commit()
            states = list(db.scalars(select(ActivityState).where(ActivityState.journey_id == journey.id)))
            assert [item.code for item in states] == ["evaluation"]
    finally:
        activate_assessment_definition(lrc)


def test_target_scoring_supports_lower_is_better():
    lrc = lrc_assessment_definition()
    definition = blank_assessment_definition()
    activity = definition.activities[0]
    activity.scoring = "target_average"
    activity.criteria[0].inputType = "duration"
    activity.criteria[0].target = Decimal("10")
    activity.criteria[0].direction = "lower"
    try:
        activate_assessment_definition(definition)
        at_target, *_ = target_activity_score("evaluation", {"performance": "10"})
        slower, *_ = target_activity_score("evaluation", {"performance": "20"})
        faster, *_ = target_activity_score("evaluation", {"performance": "5"})
        assert at_target == Decimal("5.00")
        assert slower == Decimal("2.50")
        assert faster == Decimal("5.00")
    finally:
        activate_assessment_definition(lrc)


def test_runtime_uses_current_dimension_name_after_rename():
    definition = blank_assessment_definition()
    definition.dimensions[0].name = "Client communication"
    # Reproduce an older draft whose denormalized display name was not updated.
    definition.activities[0].criteria[0].dimensionName = "Performance"

    tokens = activate_assessment_definition(definition)
    try:
        assert RUBRICS["evaluation"].criteria[0].dimension == "Client communication"
    finally:
        reset_assessment_definition(tokens)


def test_target_duration_uses_converted_score_in_criteria_dimension():
    definition = blank_assessment_definition()
    activity = definition.activities[0]
    activity.scoring = "target_average"
    activity.criteria[0].inputType = "duration"
    activity.criteria[0].target = Decimal("90")
    activity.criteria[0].direction = "lower"
    # Raw duration values are not rating-scale values.  These bounds deliberately
    # describe a wider measurement domain to catch accidental raw-value math.
    activity.criteria[0].minimum = Decimal("0")
    activity.criteria[0].maximum = Decimal("300")

    tokens = activate_assessment_definition(definition)
    try:
        with SessionLocal() as db:
            journey = create_journey(db, "Timed assessment", date(2026, 9, 4), 1, "Test owner")
            recruit = Recruit(journey_id=journey.id, name="Candidate", present=True)
            evaluator = Evaluator(
                journey_id=journey.id, name="Assessor", role="assessor", present=True,
            )
            db.add_all([recruit, evaluator])
            db.flush()
            state = db.scalar(select(ActivityState).where(
                ActivityState.journey_id == journey.id,
                ActivityState.code == "evaluation",
            ))
            round_record = AssignmentRound(
                journey_id=journey.id,
                activity_code="evaluation",
                version=1,
                status="published",
                seed="target-dimension",
                created_by="Test owner",
            )
            db.add(round_record)
            db.flush()
            assignment = Assignment(
                round_id=round_record.id,
                evaluator_id=evaluator.id,
                recruit_id=recruit.id,
                slot=1,
            )
            db.add(assignment)
            db.flush()
            state.assignment_round_id = round_record.id
            submission = save_submission(
                db,
                assignment=assignment,
                round_record=round_record,
                state=state,
                responses={},
                raw={"performance": "00:01:30"},
                comments="",
                actor_type="admin",
                actor_name="Test owner",
                submit=True,
            )
            db.flush()

            assert submission.score == Decimal("5.00")
            row = result_snapshot(db, journey)["rows"][0]
            assert row["dimensions"]["performance"]["score"] == 1.0
            assert row["overallScore"] == 100.0
    finally:
        reset_assessment_definition(tokens)


@pytest.mark.parametrize("maximum", [1, 3, 20, 100])
def test_assignment_maximum_accepts_more_than_two_and_round_trips(maximum):
    definition = blank_assessment_definition()
    definition.activities[0].assignment.maximumAssessors = maximum

    restored = AssessmentSystemDefinition.model_validate(definition.model_dump(mode="json"))

    assert restored.activities[0].assignment.maximumAssessors == maximum


def test_legacy_global_assessor_maximum_is_ignored_in_favor_of_each_activity():
    payload = blank_assessment_definition().model_dump(mode="json")
    payload["assessors"]["maximumPerParticipant"] = 2

    restored = AssessmentSystemDefinition.model_validate(payload)

    assert "maximumPerParticipant" not in restored.assessors.model_dump(mode="json")
    assert restored.activities[0].assignment.maximumAssessors == 1


@pytest.mark.parametrize("maximum", [0, 101])
def test_assignment_maximum_rejects_values_outside_safe_range(maximum):
    payload = blank_assessment_definition().model_dump(mode="json")
    payload["activities"][0]["assignment"]["maximumAssessors"] = maximum

    with pytest.raises(ValidationError):
        AssessmentSystemDefinition.model_validate(payload)


def test_criterion_weights_are_percentages_of_their_dimension_across_activities():
    payload = blank_assessment_definition().model_dump(mode="json")
    payload["activities"][0]["criteria"][0]["weight"] = "0.6"
    second = deepcopy(payload["activities"][0])
    second["key"] = "second_evaluation"
    second["name"] = "Second evaluation"
    second["criteria"][0]["key"] = "second_performance"
    second["criteria"][0]["weight"] = "0.4"
    payload["activities"].append(second)

    definition = AssessmentSystemDefinition.model_validate(payload)
    assert sum(
        criterion.weight
        for activity in definition.activities
        for criterion in activity.criteria
        if criterion.dimensionKey == "performance"
    ) == Decimal("1")

    payload["activities"][1]["criteria"][0]["weight"] = "0.3"
    with pytest.raises(ValidationError, match="Performance.*90.0%.*100%"):
        AssessmentSystemDefinition.model_validate(payload)


def test_target_activity_criterion_weights_must_total_one_hundred_percent():
    payload = blank_assessment_definition().model_dump(mode="json")
    activity = payload["activities"][0]
    activity["scoring"] = "target_average"
    activity["criteria"][0].update({
        "inputType": "duration",
        "target": "90",
        "dimensionKey": "",
        "dimensionName": "",
        "weight": "0.75",
    })
    payload["dimensions"][0].update({
        "source": "activity",
        "activityKey": "evaluation",
    })

    with pytest.raises(ValidationError, match="Evaluation.*75.00%.*100%"):
        AssessmentSystemDefinition.model_validate(payload)

    activity["criteria"][0]["weight"] = "1"
    assert AssessmentSystemDefinition.model_validate(payload).activities[0].criteria[0].target == Decimal("90")


def test_general_factors_accept_custom_keys_and_validate_steps():
    payload = blank_assessment_definition().model_dump(mode="json")
    payload["generalFactors"] = [
        {"storageKey": "professionalism", "name": "Professionalism", "maximum": "5", "step": "0.5"},
        {"storageKey": "culture_add", "name": "Culture add", "maximum": "1", "step": "0.1"},
    ]
    payload["scoring"]["components"] = [
        {"source": "dimension", "key": "performance", "weight": "0.8"},
        {"source": "general", "key": "general", "weight": "0.2"},
    ]

    definition = AssessmentSystemDefinition.model_validate(payload)
    assert [factor.storageKey for factor in definition.generalFactors] == ["professionalism", "culture_add"]

    payload["generalFactors"][1]["storageKey"] = "professionalism"
    with pytest.raises(ValidationError, match="factor keys must be unique"):
        AssessmentSystemDefinition.model_validate(payload)

    payload["generalFactors"][1].update({"storageKey": "culture_add", "step": "2"})
    with pytest.raises(ValidationError, match="step cannot be greater"):
        AssessmentSystemDefinition.model_validate(payload)


def test_general_factor_maximum_is_limited_to_one_hundred():
    payload = blank_assessment_definition().model_dump(mode="json")
    payload["generalFactors"] = [
        {"storageKey": "portfolio", "name": "Portfolio", "maximum": "100", "step": "1"},
    ]
    payload["scoring"]["components"] = [
        {"source": "dimension", "key": "performance", "weight": "0.8"},
        {"source": "general", "key": "general", "weight": "0.2"},
    ]

    assert AssessmentSystemDefinition.model_validate(payload).generalFactors[0].maximum == Decimal("100")
    payload["generalFactors"][0]["maximum"] = "100.01"
    with pytest.raises(ValidationError):
        AssessmentSystemDefinition.model_validate(payload)


def test_configured_rating_and_general_steps_must_reach_their_maximum():
    payload = blank_assessment_definition().model_dump(mode="json")
    criterion = payload["activities"][0]["criteria"][0]
    criterion.update({"minimum": "1", "maximum": "5", "step": "2"})
    assert AssessmentSystemDefinition.model_validate(payload).activities[0].criteria[0].step == Decimal("2")

    criterion["step"] = "3"
    with pytest.raises(ValidationError, match="rating step must divide evenly"):
        AssessmentSystemDefinition.model_validate(payload)

    payload = blank_assessment_definition().model_dump(mode="json")
    payload["features"]["generalAssessment"] = True
    payload["generalFactors"] = [
        {"storageKey": "interview", "name": "Interview", "maximum": "5", "step": "2"},
    ]
    payload["scoring"]["components"] = [{"source": "general", "key": "general", "weight": "1"}]
    with pytest.raises(ValidationError, match="step must divide evenly"):
        AssessmentSystemDefinition.model_validate(payload)


def test_access_profiles_accept_custom_keys_but_keep_owner_protected():
    payload = blank_assessment_definition().model_dump(mode="json")
    payload["accessProfiles"] = [
        payload["accessProfiles"][0],
        {
            "key": "panel_lead",
            "name": "Panel lead",
            "description": "Can evaluate and review results.",
            "enabled": True,
            "capabilities": ["evaluate", "results"],
        },
    ]

    definition = AssessmentSystemDefinition.model_validate(payload)
    assert definition.accessProfiles[1].key == "panel_lead"
    assert definition.accessProfiles[1].capabilities == ["evaluate", "results"]

    payload["accessProfiles"].append(deepcopy(payload["accessProfiles"][1]))
    with pytest.raises(ValidationError, match="Access profiles must be unique"):
        AssessmentSystemDefinition.model_validate(payload)

    payload["accessProfiles"] = [item for item in payload["accessProfiles"] if item["key"] != "owner"]
    with pytest.raises(ValidationError, match="include the owner"):
        AssessmentSystemDefinition.model_validate(payload)
