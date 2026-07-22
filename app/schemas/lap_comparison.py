from uuid import UUID

from pydantic import BaseModel


class ComparisonSessionResponse(BaseModel):
    id: UUID
    name: str
    session_identifier: str
    session_type: str


class SectorTimesResponse(BaseModel):
    sector_1_ms: int | None
    sector_2_ms: int | None
    sector_3_ms: int | None


class SpeedPointsResponse(BaseModel):
    speed_i1_kph: float | None
    speed_i2_kph: float | None
    finish_line_speed_kph: float | None
    speed_trap_kph: float | None


class LapStintContextResponse(BaseModel):
    stint_number: int | None
    compound: str | None
    tyre_life: float | None
    fresh_tyre: bool | None
    position: int | None
    track_status: str | None


class ComparedLapResponse(BaseModel):
    lap_id: UUID

    driver_number: str
    abbreviation: str | None
    driver_name: str

    team_name: str | None
    team_colour: str | None

    lap_number: int
    lap_time_ms: int | None

    sectors: SectorTimesResponse
    speed_points: SpeedPointsResponse
    stint_context: LapStintContextResponse

    quality_flags: list[str]


class TimingDeltaResponse(BaseModel):
    lap_time_ms: int | None
    sector_1_ms: int | None
    sector_2_ms: int | None
    sector_3_ms: int | None


class SpeedPointsDeltaResponse(BaseModel):
    speed_i1_kph: float | None
    speed_i2_kph: float | None
    finish_line_speed_kph: float | None
    speed_trap_kph: float | None


class LapComparisonDeltaResponse(BaseModel):
    convention: str
    faster_driver_number_by_lap_time: str | None

    timing: TimingDeltaResponse
    speed_points: SpeedPointsDeltaResponse


class TelemetryComparisonReadinessResponse(BaseModel):
    telemetry_ready: bool

    reference_raw_sample_count: int
    target_raw_sample_count: int

    missing_reasons: list[str]


class LapComparisonResponse(BaseModel):
    comparison_version: str
    comparison_scope: str

    reference_session: ComparisonSessionResponse
    target_session: ComparisonSessionResponse

    reference: ComparedLapResponse
    target: ComparedLapResponse

    delta: LapComparisonDeltaResponse
    telemetry: TelemetryComparisonReadinessResponse

    comparison_warnings: list[str]
    disclaimer: str