from uuid import UUID

from pydantic import BaseModel, Field


class HeadToHeadRaceResultResponse(BaseModel):
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
    driver_a_points: int
    driver_b_points: int
    winner_driver_number: str | None
    categories: list[HeadToHeadScoreCategoryResponse]


class HeadToHeadResponse(BaseModel):
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