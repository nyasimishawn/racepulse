from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class ReplaySourceResponse(BaseModel):
    race_session_id: UUID
    session_name: str
    session_type: str

    lap_id: UUID
    driver_number: str
    driver_name: str
    driver_abbreviation: str | None

    lap_number: int
    lap_time_ms: int | None
    duration_ms: int


class ReplayManifestResponse(BaseModel):
    replay_version: str
    source: ReplaySourceResponse

    telemetry_frame_count: int
    playback_speed: float
    websocket_path: str

    quality_flags: list[str]
    warnings: list[str]
    disclaimer: str


class ReplayTelemetryDataResponse(BaseModel):
    speed_kph: float
    throttle_percentage: float
    brake_applied: bool

    rpm: int | None
    gear: int | None
    drs: int | None

    x: float | None
    y: float | None
    z: float | None
    distance_m: float | None


class ReplayReadyMessage(BaseModel):
    type: Literal["replay.ready"] = "replay.ready"

    replay_version: str
    source: ReplaySourceResponse
    playback_speed: float
    from_ms: int
    telemetry_frame_count: int


class ReplayTelemetryFrameMessage(BaseModel):
    type: Literal["replay.frame"] = "replay.frame"

    sequence: int
    elapsed_time_ms: int
    source_relative_time_ms: int

    duration_ms: int
    progress_percentage: float

    telemetry: ReplayTelemetryDataResponse


class ReplayCompletedMessage(BaseModel):
    type: Literal["replay.completed"] = "replay.completed"

    duration_ms: int
    frames_sent: int


class ReplayErrorMessage(BaseModel):
    type: Literal["replay.error"] = "replay.error"

    code: str
    message: str