from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.race_context import TimelineEventType
from app.schemas.replay_map import ReplayMapFrameResponse


class CreateReplayRoomRequest(BaseModel):
    race_session_id: UUID

    playback_speed: float = Field(
        default=10.0,
        ge=0.25,
        le=100.0,
    )

    start_at_ms: int = Field(default=0, ge=0)


class ReplayRoomCommandRequest(BaseModel):
    action: Literal["PLAY", "PAUSE", "SEEK", "SET_SPEED", "STOP"]

    expected_revision: int = Field(ge=1)

    source_cursor_ms: int | None = Field(default=None, ge=0)

    playback_speed: float | None = Field(
        default=None,
        ge=0.25,
        le=100.0,
    )


class TimingTowerRowResponse(BaseModel):
    driver_number: str
    abbreviation: str | None
    driver_name: str

    team_name: str | None
    team_colour: str | None

    grid_position: int | None
    track_position: int | None

    completed_laps: int
    current_lap_number: int

    last_lap_time_ms: int | None
    best_lap_time_ms: int | None

    sector_1_time_ms: int | None
    sector_2_time_ms: int | None
    sector_3_time_ms: int | None

    stint: int | None
    compound: str | None
    tyre_life: float | None

    track_status_raw: str | None
    in_pit_lane: bool

    laps_down: int
    status: str
    final_status: str | None

    last_lap_quality_flags: list[str]


class ReplayRoomContextEventResponse(BaseModel):
    event_id: str
    event_type: TimelineEventType

    source_cursor_ms: int
    source_session_time_ms: int
    occurred_at: datetime | None

    priority: int
    title: str
    message: str
    severity: str

    driver_number: str | None = None
    lap_number: int | None = None

    data_quality_flags: list[str] = Field(
        default_factory=list
    )
    payload: dict[str, Any] = Field(default_factory=dict)


class ReplayRoomContextStateResponse(BaseModel):
    passed_event_count: int = 0
    latest_weather: ReplayRoomContextEventResponse | None = None


class ReplayRoomResponse(BaseModel):
    room_id: UUID

    race_session_id: UUID
    session_name: str
    session_type: str

    status: str
    playback_speed: float

    source_cursor_ms: int
    duration_ms: int
    timing_duration_ms: int

    revision: int
    cursor_epoch: int

    source_time_origin_ms: int

    timing_tower_driver_count: int
    replay_kind: str

    track_map_available: bool = False
    full_session_track_map_available: bool = False

    map_import_id: UUID | None = None
    map_driver_count: int = 0
    map_sample_interval_ms: int | None = None
    map_time_alignment: str | None = None

    context_available: bool = False
    context_alignment: Literal[
        "LAP_ANCHORED",
        "UNALIGNED",
        "NOT_IMPORTED",
    ] = "NOT_IMPORTED"
    frozen_context_event_count: int = 0

    websocket_path: str

    created_at_epoch_ms: int
    updated_at_epoch_ms: int

    warnings: list[str]


class ReplayRoomSnapshotResponse(BaseModel):
    room_id: UUID

    status: str
    revision: int
    cursor_epoch: int

    source_cursor_ms: int
    duration_ms: int
    progress_percentage: float

    # Timing-tower events only; context events are tracked separately.
    applied_event_count: int
    timing_semantics: str

    rows: list[TimingTowerRowResponse]
    context_state: ReplayRoomContextStateResponse

    warnings: list[str]


class ReplayRoomReadyMessage(BaseModel):
    type: Literal["room.ready"] = "room.ready"

    room: ReplayRoomResponse
    snapshot: ReplayRoomSnapshotResponse

    map_frame: ReplayMapFrameResponse | None = None

    # Only events exactly at the initial replay cursor.
    initial_context_events: list[
        ReplayRoomContextEventResponse
    ] = Field(default_factory=list)


class ReplayRoomTickMessage(BaseModel):
    type: Literal["room.tick"] = "room.tick"

    room_id: UUID
    snapshot: ReplayRoomSnapshotResponse

    map_frame: ReplayMapFrameResponse | None = None

    # Events crossed in (start, end].
    context_events: list[
        ReplayRoomContextEventResponse
    ] = Field(default_factory=list)

    event_window_start_exclusive_ms: int
    event_window_end_inclusive_ms: int


class ReplayRoomSnapshotMessage(BaseModel):
    type: Literal["room.snapshot"] = "room.snapshot"

    snapshot: ReplayRoomSnapshotResponse
    map_frame: ReplayMapFrameResponse | None = None


class ReplayRoomCompletedMessage(BaseModel):
    type: Literal["room.completed"] = "room.completed"

    snapshot: ReplayRoomSnapshotResponse
    map_frame: ReplayMapFrameResponse | None = None


class ReplayRoomErrorMessage(BaseModel):
    type: Literal["room.error"] = "room.error"

    code: str
    message: str