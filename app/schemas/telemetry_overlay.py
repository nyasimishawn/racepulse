from uuid import UUID

from pydantic import BaseModel


class TelemetryTraceBinResponse(BaseModel):
    speed_kph: float | None
    throttle_percentage: float | None
    brake_share: float | None
    rpm: float | None
    gear: int | None
    drs: int | None
    source_sample_count: int


class TelemetryOverlayDeltaResponse(BaseModel):
    speed_kph: float | None
    throttle_percentage: float | None
    brake_share: float | None


class TelemetryOverlayPointResponse(BaseModel):
    normalized_progress: float
    reference: TelemetryTraceBinResponse
    target: TelemetryTraceBinResponse
    delta: TelemetryOverlayDeltaResponse


class TelemetryTraceQualityResponse(BaseModel):
    raw_sample_count: int
    clean_sample_count: int
    usable_bin_count: int
    distance_span_m: float | None
    time_coverage_ratio: float | None


class TelemetryOverlayResponse(BaseModel):
    overlay_version: str
    eligible: bool
    alignment: str
    bin_count: int
    common_bin_count: int
    coverage_percent: float

    comparison_scope: str
    reference_session_id: UUID
    target_session_id: UUID
    reference_lap_id: UUID
    target_lap_id: UUID

    reference_quality: TelemetryTraceQualityResponse
    target_quality: TelemetryTraceQualityResponse

    distance_span_difference_percent: float | None

    warnings: list[str]
    ineligibility_reasons: list[str]
    points: list[TelemetryOverlayPointResponse]

    disclaimer: str