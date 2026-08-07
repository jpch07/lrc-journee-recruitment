from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class AdminLoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=500)
    display_name: str = Field(min_length=1, max_length=120)

    @field_validator("display_name")
    @classmethod
    def clean_display_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("Display name is required.")
        return value


class AccountLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=500)


class AccountCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=500)
    evaluator_role: Literal["overall", "dossard"] = "dossard"


class AccountUpdateRequest(BaseModel):
    can_admin: bool | None = None
    can_results: bool | None = None
    active: bool | None = None
    evaluator_role: Literal["overall", "dossard"] | None = None
    attendance_journey_ids: list[str] | None = None
    base_version: int | None = Field(default=None, ge=1)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=500)
    new_password: str = Field(min_length=8, max_length=500)


class PasswordResetRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=500)


class JourneyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    event_date: date


class JourneyUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    event_date: date | None = None
    room_count: int | None = Field(default=None, ge=1, le=100)
    status: Literal["draft", "ready", "active", "completed", "archived"] | None = None
    base_version: int | None = Field(default=None, ge=1)


class RecruitCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    phone_number: str | None = Field(default=None, max_length=40)
    date_of_birth: date | None = None


class EvaluatorCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    role: Literal["overall", "dossard"]
    add_to_directory: bool = True
    password: str | None = Field(default=None, min_length=8, max_length=500)


class RecruitAttendanceItem(BaseModel):
    id: str
    present: bool
    arrival_time: datetime | None = None
    phone_number: str | None = Field(default=None, max_length=40)
    date_of_birth: date | None = None
    attendance_comment: str = Field(default="", max_length=1000)
    active: bool = True
    base_version: int | None = None


class EvaluatorAttendanceItem(BaseModel):
    id: str
    present: bool
    role: Literal["overall", "dossard"]
    active: bool = True
    base_version: int | None = None


class RecruitAttendanceRequest(BaseModel):
    items: list[RecruitAttendanceItem]


class RecruitAttendancePatchRequest(BaseModel):
    base_version: int = Field(ge=1)
    present: bool | None = None
    arrival_time: datetime | None = None
    phone_number: str | None = Field(default=None, max_length=40)
    date_of_birth: date | None = None
    attendance_comment: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def require_change(self):
        if not (self.model_fields_set - {"base_version"}):
            raise ValueError("At least one attendance field is required.")
        return self


class EvaluatorAttendanceRequest(BaseModel):
    items: list[EvaluatorAttendanceItem]


class MandatoryRoomItem(BaseModel):
    evaluator_id: str
    room_number: int = Field(ge=1, le=100)


class MandatoryRoomRequest(BaseModel):
    items: list[MandatoryRoomItem]


class PreviewRequest(BaseModel):
    seed: str | None = Field(default=None, max_length=100)


class RoomPlanEditRequest(BaseModel):
    recruit_rooms: dict[str, int]
    evaluator_rooms: dict[str, int]


class RoomCountRequest(BaseModel):
    room_count: int = Field(ge=1, le=100)
    reason: str = Field(default="", max_length=1000)


class AssignmentEditItem(BaseModel):
    evaluator_id: str
    recruit_id: str
    slot: Literal[1, 2]
    room_number: int | None = None
    override_reason: str | None = Field(default=None, max_length=500)


class AssignmentEditRequest(BaseModel):
    items: list[AssignmentEditItem]


class ActivityActionRequest(BaseModel):
    reason: str = Field(default="", max_length=1000)


class EventDayProtectionStartRequest(BaseModel):
    duration_hours: Literal[6, 12]


class EventDayProtectionActionRequest(BaseModel):
    reason: str = Field(default="", max_length=1000)


class EvaluationSimulationRequest(BaseModel):
    count: int | None = Field(default=None, ge=1, le=200)


class GeneralAssessmentRequest(BaseModel):
    punctuality: float | None = None
    respect: float | None = None
    seriousness: float | None = None
    comment: str = Field(default="", max_length=5000)
    notes: str = Field(default="", max_length=10000)
    base_version: int | None = None


class EvaluatorSessionRequest(BaseModel):
    evaluator_id: str


class RecruitAttendanceSessionRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)

    @field_validator("display_name")
    @classmethod
    def clean_display_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("Display name is required.")
        return value


class EvaluationPayload(BaseModel):
    responses: dict[str, Any] = Field(default_factory=dict)
    comments: str = Field(default="", max_length=5000)
    raw: dict[str, Any] = Field(default_factory=dict)
    client_version: int | None = None


class AdminCorrectionRequest(EvaluationPayload):
    reason: str = Field(min_length=1, max_length=1000)


class AdminEvaluationRequest(EvaluationPayload):
    reason: str = Field(min_length=1, max_length=1000)
