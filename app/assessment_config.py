from __future__ import annotations

import re
from copy import deepcopy
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .rubric import ACTIVITY_ORDER, DIMENSION_NAMES, DIMENSION_ORDER, RUBRICS


KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


class Terminology(BaseModel):
    system: str = "Assessment System"
    session: str = "Session"
    sessionPlural: str = "Sessions"
    participant: str = "Participant"
    participantPlural: str = "Participants"
    assessor: str = "Assessor"
    assessorPlural: str = "Assessors"
    group: str = "Group"
    groupPlural: str = "Groups"
    stage: str = "Activity"
    stagePlural: str = "Activities"


class Branding(BaseModel):
    organizationName: str = ""
    shortMark: str = "AS"
    primaryColor: str = "#4f46e5"
    darkColor: str = "#172033"
    logoUrl: str = ""


class ParticipantSettings(BaseModel):
    phoneEnabled: bool = True
    dateOfBirthEnabled: bool = True
    photoEnabled: bool = True
    arrivalTimeEnabled: bool = True
    attendanceCommentEnabled: bool = True
    linkedDirectoryEnabled: bool = True
    directorySheetUrl: str = ""
    directorySheetName: str = "List of Recruits"


class AssessorCategory(BaseModel):
    key: str
    name: str
    primaryPriority: int = Field(default=0, ge=0, le=20)
    secondaryPriority: int = Field(default=0, ge=0, le=20)
    color: str = "#64748b"


class AssessorSettings(BaseModel):
    categories: list[AssessorCategory]
    maximumPerParticipant: int = Field(default=2, ge=1, le=2)
    attendanceEnabled: bool = True
    linkedDirectoryEnabled: bool = True
    directorySheetUrl: str = ""
    directorySheetName: str = "Evaluators"


class CriterionDefinition(BaseModel):
    key: str
    dimensionKey: str = ""
    dimensionName: str = ""
    name: str
    explanation: str = ""
    weight: Decimal = Field(default=Decimal("1"), gt=0)
    inputType: Literal["rating", "integer", "number", "duration"] = "rating"
    minimum: Decimal = Decimal("0")
    maximum: Decimal = Decimal("5")
    step: Decimal = Field(default=Decimal("0.1"), gt=0)
    target: Decimal | None = None
    direction: Literal["higher", "lower"] = "higher"
    unit: str = ""

    @model_validator(mode="after")
    def validate_criterion(self):
        if not KEY_PATTERN.fullmatch(self.key):
            raise ValueError(f"Invalid criterion key: {self.key}")
        if self.maximum <= self.minimum:
            raise ValueError(f"{self.name}: maximum must be greater than minimum.")
        if self.inputType in {"integer", "number", "duration"} and (
            self.target is None or self.target <= 0
        ):
            raise ValueError(f"{self.name}: numeric and duration criteria need a positive target.")
        return self


class AssignmentPolicy(BaseModel):
    mode: Literal["automatic_global", "automatic_groups", "manual"] = "automatic_global"
    maximumAssessors: int = Field(default=2, ge=1, le=2)
    avoidRepeatPairs: bool = True
    predecessor: str = ""
    reuseAssignmentsFrom: str = ""
    parallelGroup: str = ""
    mandatoryPlacements: bool = False


class ActivityDefinition(BaseModel):
    key: str
    name: str
    enabled: bool = True
    scoring: Literal["weighted_rating", "target_average"] = "weighted_rating"
    criteria: list[CriterionDefinition]
    assignment: AssignmentPolicy = Field(default_factory=AssignmentPolicy)
    evaluatorShowsDescriptions: bool = True
    evaluatorShowsDimensions: bool = True
    commentsEnabled: bool = True

    @model_validator(mode="after")
    def validate_activity(self):
        if not KEY_PATTERN.fullmatch(self.key):
            raise ValueError(f"Invalid activity key: {self.key}")
        keys = [item.key for item in self.criteria]
        if not keys or len(keys) != len(set(keys)):
            raise ValueError(f"{self.name}: criterion keys must be present and unique.")
        if self.scoring == "weighted_rating" and any(item.inputType != "rating" for item in self.criteria):
            raise ValueError(f"{self.name}: weighted rating activities accept rating criteria only.")
        if self.scoring == "target_average" and any(item.inputType == "rating" for item in self.criteria):
            raise ValueError(f"{self.name}: target activities require number, integer, or duration criteria.")
        return self


class DimensionDefinition(BaseModel):
    key: str
    name: str
    source: Literal["criteria", "activity"] = "criteria"
    activityKey: str = ""
    displayMaximum: Decimal = Field(default=Decimal("5"), gt=0)


class GeneralFactorDefinition(BaseModel):
    storageKey: Literal["punctuality", "respect", "seriousness"]
    name: str
    maximum: Decimal = Field(default=Decimal("1"), gt=0, le=5)
    step: Decimal = Field(default=Decimal("0.1"), gt=0)


class ScoreComponent(BaseModel):
    source: Literal["dimension", "general"]
    key: str
    weight: Decimal = Field(gt=0)


class PerformanceBand(BaseModel):
    key: str
    name: str
    minimum: Decimal
    color: str

    @model_validator(mode="after")
    def validate_band(self):
        if not KEY_PATTERN.fullmatch(self.key):
            raise ValueError(f"Invalid performance-band key: {self.key}")
        return self


class ScoringSettings(BaseModel):
    officialMaximum: Decimal = Field(default=Decimal("20"), gt=0)
    assessorAggregation: Literal["available_average", "missing_as_zero"] = "available_average"
    missingComponents: Literal["zero", "exclude"] = "zero"
    ranking: Literal["competition", "dense", "ordinal"] = "competition"
    components: list[ScoreComponent]
    bands: list[PerformanceBand]


class FeatureSettings(BaseModel):
    participantAttendance: bool = True
    assessorAttendance: bool = True
    groupsAndRooms: bool = True
    mandatoryPlacements: bool = True
    liveDashboard: bool = True
    managementPortal: bool = True
    attendancePortal: bool = True
    resultsAndRankings: bool = True
    participantProfiles: bool = True
    generalAssessment: bool = True
    notes: bool = True
    excelReports: bool = True
    evaluationSimulator: bool = True


class DashboardSettings(BaseModel):
    attendanceCounts: bool = True
    assessorCategories: bool = True
    activeStage: bool = True
    submissionProgress: bool = True
    warnings: bool = True
    stageLifecycle: bool = True
    provisionalRanking: bool = True
    averages: bool = True


class AccessProfile(BaseModel):
    key: Literal["owner", "administrator", "assessor", "management", "attendance"]
    name: str
    description: str = ""
    enabled: bool = True
    capabilities: list[Literal["evaluate", "admin", "results", "attendance"]] = Field(default_factory=list)


class AssessmentSystemDefinition(BaseModel):
    schemaVersion: int = 1
    name: str
    description: str = ""
    terminology: Terminology = Field(default_factory=Terminology)
    branding: Branding = Field(default_factory=Branding)
    participants: ParticipantSettings = Field(default_factory=ParticipantSettings)
    assessors: AssessorSettings
    activities: list[ActivityDefinition]
    dimensions: list[DimensionDefinition]
    generalFactors: list[GeneralFactorDefinition] = Field(default_factory=list)
    scoring: ScoringSettings
    features: FeatureSettings = Field(default_factory=FeatureSettings)
    dashboard: DashboardSettings = Field(default_factory=DashboardSettings)
    accessProfiles: list[AccessProfile] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_definition(self):
        activities = [item for item in self.activities if item.enabled]
        activity_keys = [item.key for item in activities]
        if not activities or len(activity_keys) != len(set(activity_keys)):
            raise ValueError("At least one enabled activity with a unique key is required.")
        category_keys = [item.key for item in self.assessors.categories]
        if not category_keys or len(category_keys) != len(set(category_keys)):
            raise ValueError("Assessor categories must be present and unique.")
        for category in self.assessors.categories:
            if not KEY_PATTERN.fullmatch(category.key):
                raise ValueError(f"Invalid assessor category key: {category.key}")
            if len(category.key) > 20:
                raise ValueError(f"Assessor category keys cannot exceed 20 characters: {category.key}")
        dimension_keys = [item.key for item in self.dimensions]
        if not dimension_keys or len(dimension_keys) != len(set(dimension_keys)):
            raise ValueError("Calculated dimensions must be present and unique.")
        known_dimensions = set(dimension_keys)
        known_activities = set(activity_keys)
        for dimension in self.dimensions:
            if not KEY_PATTERN.fullmatch(dimension.key):
                raise ValueError(f"Invalid dimension key: {dimension.key}")
            if dimension.source == "activity" and dimension.activityKey not in known_activities:
                raise ValueError(f"{dimension.name} refers to an unknown activity.")
        for activity in activities:
            if len(activity.key) > 30:
                raise ValueError(f"Activity keys cannot exceed 30 characters: {activity.key}")
            policy = activity.assignment
            for reference in (policy.predecessor, policy.reuseAssignmentsFrom):
                if reference and reference not in known_activities:
                    raise ValueError(f"{activity.name} refers to an unknown activity: {reference}")
            for criterion in activity.criteria:
                if criterion.dimensionKey and criterion.dimensionKey not in known_dimensions:
                    raise ValueError(f"{activity.name}/{criterion.name} refers to an unknown dimension.")
            if policy.reuseAssignmentsFrom:
                source = next(item for item in activities if item.key == policy.reuseAssignmentsFrom)
                if source.assignment.reuseAssignmentsFrom:
                    raise ValueError(f"{activity.name}: shared assignments cannot form a reuse chain.")
                if policy.mode != source.assignment.mode:
                    raise ValueError(f"{activity.name}: shared-assignment activities must use the same assignment mode.")
            if policy.mandatoryPlacements and policy.mode != "automatic_groups":
                raise ValueError(f"{activity.name}: mandatory placements require group-based assignment.")
        dependency_map = {
            item.key: item.assignment.predecessor for item in activities if item.assignment.predecessor
        }
        for starting_key in dependency_map:
            visited: set[str] = set()
            current = starting_key
            while current in dependency_map:
                if current in visited:
                    raise ValueError("Activity dependencies contain a circular reference.")
                visited.add(current)
                current = dependency_map[current]
        known_general = {item.storageKey for item in self.generalFactors}
        component_total = sum((item.weight for item in self.scoring.components), Decimal("0"))
        if component_total != Decimal("1"):
            raise ValueError(f"Overall score component weights total {component_total}; expected 1.00.")
        for component in self.scoring.components:
            if component.source == "dimension" and component.key not in known_dimensions:
                raise ValueError(f"Overall score refers to unknown dimension: {component.key}")
            if component.source == "general" and component.key != "general":
                raise ValueError("The general assessment component key must be 'general'.")
            if component.source == "general" and not known_general:
                raise ValueError("Overall score uses general assessment but no general factors exist.")
        bands = sorted(self.scoring.bands, key=lambda item: item.minimum)
        if not bands or bands[0].minimum != 0 or bands[-1].minimum > self.scoring.officialMaximum:
            raise ValueError("Performance bands must start at zero and stay within the official score scale.")
        if self.features.mandatoryPlacements and not self.features.groupsAndRooms:
            raise ValueError("Mandatory placements require Groups and Rooms to be enabled.")
        profile_keys = [item.key for item in self.accessProfiles]
        if len(profile_keys) != len(set(profile_keys)) or "owner" not in profile_keys:
            raise ValueError("Access profiles must be unique and include the owner profile.")
        owner_profile = next(item for item in self.accessProfiles if item.key == "owner")
        if not owner_profile.enabled or set(owner_profile.capabilities) != {"evaluate", "admin", "results", "attendance"}:
            raise ValueError("The owner profile must stay enabled with complete access.")
        return self


def _lrc_definition_from_current_rubric() -> AssessmentSystemDefinition:
    activities: list[ActivityDefinition] = []
    policies = {
        "sport": AssignmentPolicy(mode="automatic_global"),
        "escape_room": AssignmentPolicy(mode="automatic_groups", mandatoryPlacements=True),
        "negotiation": AssignmentPolicy(mode="automatic_groups", predecessor="escape_room", mandatoryPlacements=True),
        "skills": AssignmentPolicy(mode="automatic_groups", predecessor="negotiation", parallelGroup="skills_simulation", mandatoryPlacements=True),
        "simulation": AssignmentPolicy(mode="automatic_groups", predecessor="negotiation", reuseAssignmentsFrom="skills", parallelGroup="skills_simulation", mandatoryPlacements=True),
    }
    for key in ACTIVITY_ORDER:
        rubric = RUBRICS[key]
        activities.append(ActivityDefinition(
            key=key,
            name=rubric.name,
            scoring="target_average" if rubric.kind == "sport" else "weighted_rating",
            criteria=[CriterionDefinition(
                key=item.key,
                dimensionKey=item.dimension.casefold().replace(" ", "_") if item.dimension.casefold().replace(" ", "_") in DIMENSION_ORDER else "",
                dimensionName=item.dimension,
                name=item.name,
                explanation=item.explanation,
                weight=item.weight,
                inputType={"grade": "rating", "integer": "integer", "duration": "duration"}.get(item.input_type, "number"),
                minimum=Decimal("0"), maximum=Decimal("5"), step=Decimal("0.1"),
                target=item.target, unit=item.unit,
            ) for item in rubric.criteria],
            assignment=policies[key],
        ))
    seventh = Decimal("1") / Decimal("7")
    return AssessmentSystemDefinition(
        name="LRC Journee Recruitment 2026",
        description="The finalized Lebanese Red Cross Journee recruitment system.",
        terminology=Terminology(
            system="Journee Recruitment", session="Journee", sessionPlural="Journees",
            participant="Recruit", participantPlural="Recruits", assessor="Evaluator",
            assessorPlural="Evaluators", group="Room", groupPlural="Rooms",
            stage="Activity", stagePlural="Activities",
        ),
        branding=Branding(
            organizationName="Lebanese Red Cross", shortMark="LRC",
            primaryColor="#b20d2d", darkColor="#192331",
        ),
        participants=ParticipantSettings(),
        assessors=AssessorSettings(categories=[
            AssessorCategory(key="overall", name="Overall", primaryPriority=0, secondaryPriority=1, color="#b20d2d"),
            AssessorCategory(key="dossard", name="Dossard", primaryPriority=1, secondaryPriority=0, color="#23384d"),
        ]),
        activities=activities,
        dimensions=[
            DimensionDefinition(key=key, name=DIMENSION_NAMES[key], source="activity" if key in {"application", "physical_ability"} else "criteria",
                                activityKey={"application": "skills", "physical_ability": "sport"}.get(key, ""))
            for key in DIMENSION_ORDER
        ],
        generalFactors=[
            GeneralFactorDefinition(storageKey="punctuality", name="Punctuality"),
            GeneralFactorDefinition(storageKey="respect", name="Respect to us"),
            GeneralFactorDefinition(storageKey="seriousness", name="Seriousness"),
        ],
        scoring=ScoringSettings(
            officialMaximum=Decimal("20"),
            assessorAggregation="available_average",
            missingComponents="zero",
            components=[
                *[ScoreComponent(source="dimension", key=key, weight=seventh) for key in DIMENSION_ORDER],
                ScoreComponent(source="general", key="general", weight=seventh),
            ],
            bands=[
                PerformanceBand(key="red", name="Red", minimum=Decimal("0"), color="#c8102e"),
                PerformanceBand(key="yellow", name="Yellow", minimum=Decimal("13"), color="#eab308"),
                PerformanceBand(key="green", name="Green", minimum=Decimal("16"), color="#16a34a"),
            ],
        ),
        accessProfiles=[
            AccessProfile(key="owner", name="Owner", capabilities=["evaluate", "admin", "results", "attendance"]),
            AccessProfile(key="administrator", name="Administrator", capabilities=["evaluate", "admin", "results", "attendance"]),
            AccessProfile(key="assessor", name="Evaluator", capabilities=["evaluate"]),
            AccessProfile(key="management", name="Management viewer", capabilities=["results"]),
            AccessProfile(key="attendance", name="Recruit attendance operator", capabilities=["attendance"]),
        ],
    )


_LRC_JSON = _lrc_definition_from_current_rubric().model_dump(mode="json")


def lrc_assessment_definition() -> AssessmentSystemDefinition:
    return AssessmentSystemDefinition.model_validate(deepcopy(_LRC_JSON))


def blank_assessment_definition() -> AssessmentSystemDefinition:
    return AssessmentSystemDefinition(
        name="New Assessment System",
        description="Configure the people, activities, assignments, and scoring used by this assessment.",
        terminology=Terminology(),
        branding=Branding(),
        participants=ParticipantSettings(),
        assessors=AssessorSettings(categories=[
            AssessorCategory(key="primary", name="Primary", primaryPriority=0, secondaryPriority=1),
            AssessorCategory(key="secondary", name="Secondary", primaryPriority=1, secondaryPriority=0),
        ]),
        activities=[ActivityDefinition(
            key="evaluation", name="Evaluation", criteria=[CriterionDefinition(
                key="performance", dimensionKey="performance", dimensionName="Performance",
                name="Performance", explanation="Rate the participant's demonstrated performance.",
            )],
        )],
        dimensions=[DimensionDefinition(key="performance", name="Performance", source="criteria")],
        generalFactors=[],
        scoring=ScoringSettings(
            officialMaximum=Decimal("100"),
            components=[ScoreComponent(source="dimension", key="performance", weight=Decimal("1"))],
            bands=[
                PerformanceBand(key="needs_review", name="Needs review", minimum=Decimal("0"), color="#dc2626"),
                PerformanceBand(key="meets", name="Meets expectations", minimum=Decimal("60"), color="#eab308"),
                PerformanceBand(key="strong", name="Strong", minimum=Decimal("80"), color="#16a34a"),
            ],
        ),
        features=FeatureSettings(groupsAndRooms=False, mandatoryPlacements=False),
        accessProfiles=[
            AccessProfile(key="owner", name="Owner", capabilities=["evaluate", "admin", "results", "attendance"]),
            AccessProfile(key="administrator", name="Administrator", capabilities=["evaluate", "admin", "results", "attendance"]),
            AccessProfile(key="assessor", name="Assessor", capabilities=["evaluate"]),
            AccessProfile(key="management", name="Management viewer", capabilities=["results"]),
            AccessProfile(key="attendance", name="Attendance operator", capabilities=["attendance"]),
        ],
    )


def neutralize_legacy_blank_branding(
    definition: AssessmentSystemDefinition,
) -> AssessmentSystemDefinition:
    """Render early blank workspaces with today's neutral platform branding.

    The first configurable-workspace release persisted the old LRC red as the
    default even when no organization was selected.  Keep explicitly branded
    systems (including LRC) byte-for-byte unchanged and normalize only that
    recognizable blank-workspace combination in the returned definition.
    """
    branding = definition.branding
    if (
        not branding.organizationName.strip()
        and branding.shortMark.strip().casefold() == "as"
        and branding.primaryColor.casefold() in {"#b20d2d", "#c8102e"}
    ):
        return definition.model_copy(update={
            "branding": branding.model_copy(update={
                "primaryColor": "#4f46e5",
                "darkColor": "#172033",
            })
        })
    return definition
