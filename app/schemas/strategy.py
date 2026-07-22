from uuid import UUID

from pydantic import BaseModel


class StintStrategyResponse(BaseModel):
    sequence: int
    source_stint_number: int | None

    compound: str | None
    fresh_tyre: bool | None

    start_lap: int
    end_lap: int
    lap_count: int

    tyre_life_start: float | None
    tyre_life_end: float | None

    start_position: int | None
    end_position: int | None

    pit_in_lap: int | None
    pit_out_lap: int | None

    clean_lap_count: int
    clean_median_lap_time_ms: float | None
    clean_fastest_lap_time_ms: int | None

    data_quality_flags: list[str]


class DriverStrategyResponse(BaseModel):
    driver_number: str
    abbreviation: str | None
    driver_name: str

    team_name: str | None
    team_colour: str | None

    finishing_position: int | None
    classified_position: str | None

    stints: list[StintStrategyResponse]
    data_quality_flags: list[str]


class RaceStrategyResponse(BaseModel):
    race_session_id: UUID
    session_name: str
    data_source: str

    driver_count: int
    drivers: list[DriverStrategyResponse]

    disclaimer: str