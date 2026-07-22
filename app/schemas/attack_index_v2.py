from uuid import UUID

from pydantic import BaseModel


class TelemetryEffortComponentsV2Response(BaseModel):
    reference_full_throttle_share: float
    race_full_throttle_share: float
    full_throttle_share_delta: float

    reference_brake_zone_share: float
    race_brake_zone_share: float
    brake_zone_share_delta: float

    reference_coast_candidate_distance_m: float
    race_coast_candidate_distance_m: float
    extra_coast_candidate_distance_m: float

    reference_sample_count: int
    race_sample_count: int
    common_bin_count: int


class TelemetryEffortV2Response(BaseModel):
    score_10: float
    effort_band: str
    telemetry_data_confidence: str
    components: TelemetryEffortComponentsV2Response


class PaceContextV2Response(BaseModel):
    race_lap_time_ms: int | None
    qualifying_lap_time_ms: int | None
    delta_to_qualifying_ms: int | None
    delta_to_qualifying_percent: float | None

    same_stint_baseline_lap_time_ms: float | None
    delta_to_same_stint_baseline_ms: float | None
    delta_to_same_stint_baseline_percent: float | None
    same_stint_pace_band: str | None

    baseline_selection: str
    baseline_sample_count: int


class StintContextV2Response(BaseModel):
    stint_number: int | None
    compound: str | None
    tyre_life: float | None
    fresh_tyre: bool | None
    position: int | None
    track_status: str | None


class AttackIndexV2Response(BaseModel):
    metric_version: str

    eligible: bool

    race_session_id: UUID
    qualifying_session_id: UUID | None
    driver_number: str

    race_lap_id: UUID
    race_lap_number: int
    qualifying_lap_id: UUID | None
    qualifying_lap_number: int | None
    qualifying_reference_quality: str | None

    telemetry_effort: TelemetryEffortV2Response | None
    pace_context: PaceContextV2Response
    stint_context: StintContextV2Response

    ineligibility_reasons: list[str]
    disclaimer: str