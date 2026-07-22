from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.attack_index_v2 import (
    PaceContextV2Response,
    TelemetryEffortV2Response,
)


class PushManageQualifyingReferenceResponse(BaseModel):
    qualifying_session_id: UUID | None
    qualifying_lap_id: UUID | None
    qualifying_lap_number: int | None
    qualifying_reference_quality: str | None

    qualifying_reference_available: bool
    qualifying_reference_has_stored_telemetry: bool

    unavailable_reason: str | None


class PushManageCoverageResponse(BaseModel):
    total_requested_laps: int

    race_laps_with_stored_telemetry: int
    telemetry_candidate_lap_count: int
    scored_lap_count: int
    unscored_lap_count: int
    race_laps_without_stored_telemetry: int


class PushManageContextEventResponse(BaseModel):
    event_id: str
    event_type: str

    session_time_ms: int | None
    occurred_at: str | None

    title: str
    message: str
    severity: str

    driver_number: str | None
    lap_number: int | None

    data_quality_flags: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


class PushManageTimelineEntryResponse(BaseModel):
    lap_id: UUID
    lap_number: int

    lap_start_session_time_ms: int | None
    lap_end_session_time_ms: int | None

    lap_time_ms: int | None
    sector_1_time_ms: int | None
    sector_2_time_ms: int | None
    sector_3_time_ms: int | None

    stint_number: int | None
    compound: str | None
    tyre_life: float | None
    fresh_tyre: bool | None
    position: int | None
    track_status: str | None

    telemetry_eligible: bool
    attack_index_metric_version: str | None

    telemetry_effort: TelemetryEffortV2Response | None
    pace_context: PaceContextV2Response | None

    observed_mode: str
    evidence_tags: list[str] = Field(default_factory=list)

    ineligibility_reasons: list[str] = Field(default_factory=list)
    lap_quality_flags: list[str] = Field(default_factory=list)

    context_event_ids: list[str] = Field(default_factory=list)


class PushManageTimelineResponse(BaseModel):
    timeline_version: str

    race_session_id: UUID
    session_name: str

    driver_number: str
    abbreviation: str | None
    driver_name: str

    requested_start_lap: int | None
    requested_end_lap: int | None

    qualifying_reference: PushManageQualifyingReferenceResponse
    coverage: PushManageCoverageResponse

    mode_counts: dict[str, int]
    context_events: list[PushManageContextEventResponse]

    entries: list[PushManageTimelineEntryResponse]

    warnings: list[str] = Field(default_factory=list)
    disclaimer: str