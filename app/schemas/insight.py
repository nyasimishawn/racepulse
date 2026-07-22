from uuid import UUID

from pydantic import BaseModel, Field


class SessionPaceBenchmarksResponse(BaseModel):
    session_best_clean_lap_time_ms: int | None

    session_best_sector_1_time_ms: int | None
    session_best_sector_2_time_ms: int | None
    session_best_sector_3_time_ms: int | None

    drivers_with_clean_laps: int


class DriverConsistencyResponse(BaseModel):
    sample_count: int
    median_absolute_deviation_ms: float | None
    p90_p10_spread_ms: float | None
    score_10: float | None
    data_quality_flags: list[str] = Field(default_factory=list)


class SectorStrengthResponse(BaseModel):
    best_sector_1_time_ms: int | None
    best_sector_2_time_ms: int | None
    best_sector_3_time_ms: int | None

    delta_to_session_best_sector_1_ms: int | None
    delta_to_session_best_sector_2_ms: int | None
    delta_to_session_best_sector_3_ms: int | None


class TyrePaceProxyResponse(BaseModel):
    classification: str
    estimated_pace_change_ms_per_axis_unit: float | None
    estimated_pace_change_over_stint_ms: int | None
    data_quality_flags: list[str] = Field(default_factory=list)


class RacePaceLapSeriesPointResponse(BaseModel):
    lap_number: int
    lap_time_ms: int
    tyre_life: float | None
    delta_to_stint_median_ms: float | None
    included_in_trend: bool


class StintRacePaceResponse(BaseModel):
    sequence: int
    source_stint_number: int | None

    compound: str | None
    fresh_tyre: bool | None

    start_lap: int
    end_lap: int
    raw_lap_count: int

    tyre_life_start: float | None
    tyre_life_end: float | None

    clean_lap_count: int
    trend_lap_count: int
    outlier_excluded_lap_count: int

    median_clean_lap_time_ms: float | None
    fastest_clean_lap_time_ms: int | None

    opening_pace_ms: float | None
    closing_pace_ms: float | None

    trend_axis: str
    net_pace_trend_ms_per_axis_unit: float | None
    net_pace_change_ms: int | None
    trend_direction: str
    trend_fit_r_squared: float | None
    trend_residual_mad_ms: float | None
    sample_confidence: str

    tyre_pace_proxy: TyrePaceProxyResponse

    data_quality_flags: list[str] = Field(default_factory=list)
    lap_series: list[RacePaceLapSeriesPointResponse] = Field(
        default_factory=list
    )


class RacePaceDriverResponse(BaseModel):
    driver_number: str
    abbreviation: str | None
    driver_name: str

    team_name: str | None
    team_colour: str | None

    grid_position: int | None
    finishing_position: int | None
    positions_gained: int | None
    result_status: str | None

    clean_lap_count: int
    median_clean_lap_time_ms: float | None
    fastest_clean_lap_time_ms: int | None
    pace_delta_to_session_best_ms: float | None

    consistency: DriverConsistencyResponse
    sector_strength: SectorStrengthResponse
    stints: list[StintRacePaceResponse] = Field(default_factory=list)

    data_quality_flags: list[str] = Field(default_factory=list)


class RacePaceInsightResponse(BaseModel):
    metric_version: str

    race_session_id: UUID
    session_name: str
    session_type: str

    data_source: str
    driver_count: int

    clean_lap_definition: list[str]
    session_benchmarks: SessionPaceBenchmarksResponse
    drivers: list[RacePaceDriverResponse]

    disclaimer: str