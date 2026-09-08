from uuid import UUID

from pydantic import BaseModel, Field
from app.schemas.race_context import PitEventResponse, RaceControlEventResponse


class HeadToHeadRaceResultResponse(BaseModel):
    positions_gained: int | None = None
    finishing_position: int | None
    classified_position: str | None
    grid_position: int | None
    status: str | None
    official_points: float | None


class HeadToHeadQualifyingResponse(BaseModel):
    qualifying_session_id: UUID | None
    position: int | None
    classified_position: str | None
    best_available_time_ms: int | None
    time_segment: str | None
    data_quality_flags: list[str] = Field(default_factory=list)


class HeadToHeadPaceResponse(BaseModel):
    clean_lap_count: int
    median_clean_lap_time_ms: float | None
    fastest_clean_lap_time_ms: int | None

    median_compound_relative_delta_ms: float | None
    compound_relative_sample_count: int

    sector_1_median_ms: float | None
    sector_2_median_ms: float | None
    sector_3_median_ms: float | None

    consistency_mad_ms: float | None
    data_quality_flags: list[str] = Field(default_factory=list)


class HeadToHeadStintResponse(BaseModel):
    sequence: int
    source_stint_number: int | None
    compound: str | None
    fresh_tyre: bool | None

    start_lap: int
    end_lap: int
    lap_count: int

    tyre_life_start: float | None
    tyre_life_end: float | None

    pit_in_lap: int | None
    pit_out_lap: int | None

    clean_lap_count: int
    clean_median_lap_time_ms: float | None
    clean_fastest_lap_time_ms: int | None

    data_quality_flags: list[str] = Field(default_factory=list)


class HeadToHeadPitStopProxyResponse(BaseModel):
    observed_pit_entry_lap_numbers: list[int]
    observed_pit_exit_lap_numbers: list[int]
    inferred_stop_count: int
    data_quality_flags: list[str] = Field(default_factory=list)


class HeadToHeadDriverResponse(BaseModel):
    pit_events: list[PitEventResponse] = Field(default_factory=list)
    driver_number: str
    abbreviation: str | None
    driver_name: str

    team_name: str | None
    team_colour: str | None

    race_result: HeadToHeadRaceResultResponse
    qualifying: HeadToHeadQualifyingResponse
    pace: HeadToHeadPaceResponse
    stints: list[HeadToHeadStintResponse]
    pit_stop_proxy: HeadToHeadPitStopProxyResponse

    data_quality_flags: list[str] = Field(default_factory=list)


class HeadToHeadSharedCompoundPaceResponse(BaseModel):
    compound: str

    driver_a_sample_count: int
    driver_b_sample_count: int

    driver_a_robust_fastest_lap_time_ms: float
    driver_b_robust_fastest_lap_time_ms: float
    driver_b_minus_driver_a_ms: float
    faster_driver_number: str | None

    data_quality_flags: list[str] = Field(default_factory=list)


class HeadToHeadPaceDeltaResponse(BaseModel):
    convention: str

    median_clean_lap_time_ms: float | None
    median_compound_relative_delta_ms: float | None

    sector_1_median_ms: float | None
    sector_2_median_ms: float | None
    sector_3_median_ms: float | None

    consistency_mad_ms: float | None


class HeadToHeadScoreCategoryResponse(BaseModel):
    category: str
    max_points: int
    winner_driver_number: str | None
    driver_a_points: int
    driver_b_points: int
    basis: str
    data_quality_flags: list[str] = Field(default_factory=list)


class HeadToHeadScoreResponse(BaseModel):
    score_type: str = "DRIVER_COMPARISON"
    fantasy_points_awarded: int = 0
    driver_a_points: int
    driver_b_points: int
    winner_driver_number: str | None
    categories: list[HeadToHeadScoreCategoryResponse]


class QualifyingComparisonResponse(BaseModel):
    common_segment: str | None = None
    driver_a_time_ms: int | None = None
    driver_b_time_ms: int | None = None
    driver_b_minus_driver_a_ms: int | None = None
    disclaimer: str = "Gap uses the latest segment with valid times for both drivers."


class SectorComparisonResponse(BaseModel):
    sector: int
    driver_a_median_ms: float | None
    driver_b_median_ms: float | None
    driver_a_sample_count: int
    driver_b_sample_count: int
    winner_driver_number: str | None


class TelemetryLapCoverageResponse(BaseModel):
    lap_id: UUID
    lap_number: int
    compound: str | None
    sample_count: int


class HeadToHeadTelemetryCoverage(BaseModel):
    both_drivers_have_coverage: bool
    driver_a_laps: list[TelemetryLapCoverageResponse]
    driver_b_laps: list[TelemetryLapCoverageResponse]
    disclaimer: str = (
        "Candidates are clean laps with at least 50 complete, non-interpolated "
        "car telemetry samples. "
        "A selected pair must also pass the overlay's distance/time coverage "
        "checks before traces are displayed."
    )


class HeadToHeadContextResponse(BaseModel):
    race_control_events: list[RaceControlEventResponse]
    events_truncated: bool
    weather_sample_count: int
    rainfall_observed: bool | None
    data_quality_flags: list[str]


class HeadToHeadResponse(BaseModel):
    qualifying_comparison: QualifyingComparisonResponse | None = None
    sector_comparisons: list[SectorComparisonResponse] = Field(default_factory=list)
    telemetry_coverage: HeadToHeadTelemetryCoverage | None = None
    race_context: HeadToHeadContextResponse | None = None
    comparison_version: str

    race_session_id: UUID
    meeting_id: UUID
    meeting_name: str
    race_session_name: str

    driver_a: HeadToHeadDriverResponse
    driver_b: HeadToHeadDriverResponse

    shared_compound_pace: list[HeadToHeadSharedCompoundPaceResponse]
    pace_delta: HeadToHeadPaceDeltaResponse
    score: HeadToHeadScoreResponse

    score_definition: list[str]
    data_quality_flags: list[str] = Field(default_factory=list)
    disclaimer: str
