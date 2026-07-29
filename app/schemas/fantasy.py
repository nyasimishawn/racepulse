from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
)


class FantasyPredictionAnswerInput(BaseModel):
    question_key: str = Field(min_length=1, max_length=80)
    driver_id: UUID | None = None
    team_id: UUID | None = None
    driver_ids: list[UUID] | None = Field(default=None, max_length=2)

    @field_validator("question_key")
    @classmethod
    def normalize_question_key(cls, value: str) -> str:
        normalized = value.strip().upper()

        if not normalized:
            raise ValueError("Question key must not be blank.")

        return normalized

    @field_validator("driver_ids")
    @classmethod
    def validate_driver_ids(
        cls,
        value: list[UUID] | None,
    ) -> list[UUID] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("Driver selections must be unique.")

        return value


class FantasyPredictionUpdateRequest(BaseModel):
    answers: list[FantasyPredictionAnswerInput] = Field(
        default_factory=list,
        max_length=20,
    )
    clear_question_keys: list[str] = Field(
        default_factory=list,
        max_length=20,
    )

    @field_validator("clear_question_keys")
    @classmethod
    def normalize_clear_question_keys(
        cls,
        values: list[str],
    ) -> list[str]:
        normalized = [value.strip().upper() for value in values]

        if any(not value for value in normalized):
            raise ValueError("Question keys must not be blank.")

        if len(normalized) != len(set(normalized)):
            raise ValueError("Question keys must be unique.")

        return normalized

    @model_validator(mode="after")
    def validate_question_sets(self) -> "FantasyPredictionUpdateRequest":
        answer_keys = [answer.question_key for answer in self.answers]

        if len(answer_keys) != len(set(answer_keys)):
            raise ValueError("Each question can only be answered once.")

        if set(answer_keys).intersection(self.clear_question_keys):
            raise ValueError(
                "A question cannot be answered and cleared together."
            )

        return self


class FantasyQuestionResolutionRequest(BaseModel):
    status: Literal["RESOLVED", "NOT_SCORED"]
    actual_driver_id: UUID | None = None
    actual_team_id: UUID | None = None
    actual_driver_ids: list[UUID] | None = Field(
        default=None,
        max_length=30,
    )
    source_reference: str | None = Field(
        default=None,
        max_length=1000,
    )
    data_quality_flags: list[str] = Field(
        default_factory=list,
        max_length=20,
    )

    @field_validator("actual_driver_ids")
    @classmethod
    def validate_actual_driver_ids(
        cls,
        value: list[UUID] | None,
    ) -> list[UUID] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("Driver selections must be unique.")

        return value

    @field_validator("source_reference")
    @classmethod
    def normalize_source_reference(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        normalized = value.strip()
        return normalized or None

    @field_validator("data_quality_flags")
    @classmethod
    def normalize_data_quality_flags(
        cls,
        values: list[str],
    ) -> list[str]:
        normalized = [value.strip().upper() for value in values]

        if any(not value for value in normalized):
            raise ValueError("Data-quality flags must not be blank.")

        if len(normalized) != len(set(normalized)):
            raise ValueError("Data-quality flags must be unique.")

        return normalized


class FantasyGroupCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    max_members: int = Field(default=22, ge=2, le=22)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError("Group name must not be blank.")

        return normalized


class FantasyGroupJoinRequest(BaseModel):
    invite_code: str = Field(min_length=6, max_length=32)

    @field_validator("invite_code")
    @classmethod
    def normalize_invite_code(cls, value: str) -> str:
        normalized = value.strip().upper()

        if not normalized:
            raise ValueError("Invite code must not be blank.")

        return normalized


class FantasyPickScoreResponse(BaseModel):
    status: Literal["PENDING", "SCORED", "NOT_SCORED"]
    points_awarded: int
    max_points: int
    is_exact: bool
    breakdown: dict[str, object]
    scored_at: datetime | None


class FantasyPredictionAnswerResponse(BaseModel):
    question_key: str
    driver_id: UUID | None
    team_id: UUID | None
    driver_ids: list[UUID]
    updated_at: datetime
    score: FantasyPickScoreResponse | None


class FantasyQuestionResolutionResponse(BaseModel):
    question_key: str
    status: Literal["PENDING", "RESOLVED", "NOT_SCORED"]
    resolution_source: str
    actual_driver_id: UUID | None
    actual_team_id: UUID | None
    actual_driver_ids: list[UUID]
    source_reference: str | None
    data_quality_flags: list[str]
    resolved_at: datetime | None


class FantasyQuestionResponse(BaseModel):
    key: str
    label: str
    category: Literal["PRACTICE", "QUALIFYING", "RACE"]
    answer_kind: Literal["DRIVER", "TEAM", "DRIVER_LIST"]
    target_session_id: UUID
    target_session_name: str
    locks_at: datetime | None
    state: Literal["OPEN", "LOCKED", "UNAVAILABLE"]
    max_points: int
    selection_limit: int
    answer: FantasyPredictionAnswerResponse | None
    resolution: FantasyQuestionResolutionResponse | None


class FantasyDriverOptionResponse(BaseModel):
    id: UUID
    driver_number: str
    abbreviation: str | None
    display_name: str
    team_id: UUID | None
    team_name: str | None


class FantasyTeamOptionResponse(BaseModel):
    id: UUID
    name: str
    colour: str | None


class FantasyRaceSummaryResponse(BaseModel):
    race_session_id: UUID
    meeting_id: UUID
    meeting_name: str
    year: int
    session_name: str
    started_at: datetime | None


class FantasyPredictionResponse(BaseModel):
    prediction_id: UUID | None
    race: FantasyRaceSummaryResponse
    total_points: int
    questions: list[FantasyQuestionResponse]
    drivers: list[FantasyDriverOptionResponse]
    teams: list[FantasyTeamOptionResponse]


class FantasyScoreRunResponse(BaseModel):
    race_session_id: UUID
    resolved_question_keys: list[str]
    pending_question_keys: list[str]
    updated_pick_count: int


class FantasyFinalizeResponse(BaseModel):
    race_session_id: UUID
    finalized_group_count: int
    created_or_updated_result_count: int


class FantasyLeaderboardRowResponse(BaseModel):
    rank: int
    display_name: str
    points: int
    exact_podium_hits: int
    exact_qualifying_hits: int
    is_current_user: bool


class FantasyLeaderboardResponse(BaseModel):
    scope: Literal["GLOBAL", "GROUP"]
    group_id: UUID | None
    race_session_id: UUID | None
    year: int | None
    rows: list[FantasyLeaderboardRowResponse]


class FantasyGroupSummaryResponse(BaseModel):
    id: UUID
    name: str
    owner_display_name: str
    member_count: int
    max_members: int
    is_owner: bool
    joined_at: datetime | None


class FantasyGroupMemberResponse(BaseModel):
    profile_id: UUID
    display_name: str
    is_owner: bool
    joined_at: datetime


class FantasyGroupDetailResponse(BaseModel):
    id: UUID
    name: str
    owner_display_name: str
    member_count: int
    max_members: int
    invite_code: str | None
    members: list[FantasyGroupMemberResponse]


class FantasyGroupInviteCodeResponse(BaseModel):
    group_id: UUID
    invite_code: str


class FantasyGroupPodiumRowResponse(BaseModel):
    rank: int
    display_name: str
    points: int
    exact_podium_hits: int
    exact_qualifying_hits: int


class FantasyGroupPodiumResponse(BaseModel):
    group_id: UUID
    race_session_id: UUID
    rows: list[FantasyGroupPodiumRowResponse]


class FantasyCommunityOptionResponse(BaseModel):
    option_id: UUID
    label: str
    selection_count: int
    percentage_of_predictions: float


class FantasyCommunityResponse(BaseModel):
    question_key: str
    total_predictions: int
    multiple_selection: bool
    options: list[FantasyCommunityOptionResponse]