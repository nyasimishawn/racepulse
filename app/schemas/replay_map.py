from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class ReplayMapDriverPositionResponse(BaseModel):
    driver_number: str
    abbreviation: str | None
    driver_name: str

    import_status: str
    map_state: Literal[
        "LIVE",
        "STALE",
        "NOT_STARTED",
        "UNAVAILABLE",
    ]

    sample_session_time_ms: int | None
    source_age_ms: int | None

    x: float | None
    y: float | None
    z: float | None

    position_status: str | None


class ReplayMapFrameResponse(BaseModel):
    map_import_id: UUID
    race_session_id: UUID

    source_time_origin_ms: int
    source_cursor_ms: int
    source_session_time_ms: int

    sample_interval_ms: int
    alignment: str

    drivers: list[ReplayMapDriverPositionResponse]