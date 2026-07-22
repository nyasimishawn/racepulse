from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class TimelineEventType(str, Enum):
    RACE_CONTROL = "RACE_CONTROL"
    PIT_ENTRY = "PIT_ENTRY"
    PIT_EXIT = "PIT_EXIT"
    WEATHER = "WEATHER"


class RaceContextImportResponse(BaseModel):
    race_session_id: UUID
    source: str

    weather_samples_imported: int
    race_control_events_imported: int
    pit_events_available: int

    session_clock_alignment: str
    session_clock_anchor_at: datetime | None

    warning: str | None = None


class WeatherSampleResponse(BaseModel):
    id: UUID
    source: str

    session_time_ms: int
    occurred_at: datetime | None

    air_temperature_c: float | None
    track_temperature_c: float | None
    humidity_percent: float | None
    pressure_hpa: float | None
    rainfall: bool | None
    wind_speed_mps: float | None
    wind_direction_deg: int | None


class RaceControlEventResponse(BaseModel):
    id: UUID
    source: str

    occurred_at: datetime
    session_time_ms: int | None

    category: str | None
    message: str
    status: str | None
    flag: str | None
    scope: str | None

    sector_number: int | None
    driver_number: str | None
    lap_number: int | None


class PitEventResponse(BaseModel):
    event_id: str
    event_type: TimelineEventType

    session_time_ms: int
    occurred_at: datetime | None

    driver_number: str
    abbreviation: str | None
    driver_name: str

    lap_id: UUID
    lap_number: int

    paired_event_id: str | None
    pit_lane_duration_ms: int | None

    data_quality_flags: list[str]


class SessionTimelineEventResponse(BaseModel):
    event_id: str
    event_type: TimelineEventType

    session_time_ms: int | None
    occurred_at: datetime | None

    priority: int
    title: str
    message: str
    severity: str

    driver_number: str | None = None
    lap_number: int | None = None

    data_quality_flags: list[str]
    payload: dict[str, Any]


class SessionTimelineResponse(BaseModel):
    race_session_id: UUID
    session_clock_alignment: str

    total: int
    events: list[SessionTimelineEventResponse]

    warnings: list[str]