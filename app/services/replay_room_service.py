import asyncio
import json
import time
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Any, Callable
from uuid import UUID, uuid4

from redis.asyncio import Redis
from redis.exceptions import WatchError
from sqlalchemy import select

from app.core.config import settings
from app.db.database import SessionLocal
from app.models.driver import Driver
from app.models.lap import Lap
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.team import Team
from app.schemas.replay_room import (
    CreateReplayRoomRequest,
    ReplayRoomCommandRequest,
    ReplayRoomContextEventResponse,
    ReplayRoomContextStateResponse,
    ReplayRoomResponse,
    ReplayRoomSnapshotResponse,
    TimingTowerRowResponse,
)
from app.services.replay_room_plan_service import (
    ReplayRoomPlanService,
)




ROOM_TTL_SECONDS = 86_400

REPLAY_KIND = "TIMING_TOWER_LAP_BOUNDARY"
TIMING_SEMANTICS = "LAP_CROSSING_DERIVED"


class ReplaySourceSessionNotFoundError(LookupError):
    pass


class NonRaceReplaySessionError(ValueError):
    pass


class ReplayRoomNotFoundError(LookupError):
    pass


class ReplayRoomInvalidRequestError(ValueError):
    pass


class ReplayRoomRevisionConflict(RuntimeError):
    def __init__(self, actual_revision: int) -> None:
        self.actual_revision = actual_revision

        super().__init__(
            "This room changed before your command was applied."
        )


@dataclass(frozen=True)
class _RoomState:
    room_id: str
    race_session_id: str

    status: str
    playback_speed: float

    anchor_position_ms: int
    anchor_epoch_ms: int
    duration_ms: int

    revision: int
    cursor_epoch: int

    created_at_epoch_ms: int
    updated_at_epoch_ms: int

    @classmethod
    def from_mapping(
        cls,
        values: dict[str, str],
    ) -> "_RoomState":
        return cls(
            room_id=values["room_id"],
            race_session_id=values["race_session_id"],
            status=values["status"],
            playback_speed=float(values["playback_speed"]),
            anchor_position_ms=int(
                values["anchor_position_ms"]
            ),
            anchor_epoch_ms=int(values["anchor_epoch_ms"]),
            duration_ms=int(values["duration_ms"]),
            revision=int(values["revision"]),
            cursor_epoch=int(values.get("cursor_epoch", "1")),
            created_at_epoch_ms=int(
                values["created_at_epoch_ms"]
            ),
            updated_at_epoch_ms=int(
                values["updated_at_epoch_ms"]
            ),
        )

    def to_mapping(self) -> dict[str, str]:
        return {
            "room_id": self.room_id,
            "race_session_id": self.race_session_id,
            "status": self.status,
            "playback_speed": str(self.playback_speed),
            "anchor_position_ms": str(self.anchor_position_ms),
            "anchor_epoch_ms": str(self.anchor_epoch_ms),
            "duration_ms": str(self.duration_ms),
            "revision": str(self.revision),
            "cursor_epoch": str(self.cursor_epoch),
            "created_at_epoch_ms": str(
                self.created_at_epoch_ms
            ),
            "updated_at_epoch_ms": str(
                self.updated_at_epoch_ms
            ),
        }

class ReplayRoomService:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def create_room(
        self,
        request: CreateReplayRoomRequest,
    ) -> ReplayRoomResponse:
        plan = await asyncio.to_thread(
            self._build_session_plan,
            request.race_session_id,
        )

        duration_ms = int(plan["duration_ms"])

        if request.start_at_ms > duration_ms:
            raise ReplayRoomInvalidRequestError(
                "start_at_ms cannot be after the session duration."
            )

        room_id = str(uuid4())
        now_ms = self._now_ms()

        state = _RoomState(
            room_id=room_id,
            race_session_id=str(request.race_session_id),
            status="PAUSED",
            playback_speed=request.playback_speed,
            anchor_position_ms=request.start_at_ms,
            anchor_epoch_ms=0,
            duration_ms=duration_ms,
            revision=1,
            cursor_epoch=1,
            created_at_epoch_ms=now_ms,
            updated_at_epoch_ms=now_ms,
        )

        await self.redis.set(
            self._plan_key(room_id),
            json.dumps(plan, separators=(",", ":")),
            ex=ROOM_TTL_SECONDS,
        )

        await self.redis.hset(
            self._state_key(room_id),
            mapping=state.to_mapping(),
        )

        await self.redis.expire(
            self._state_key(room_id),
            ROOM_TTL_SECONDS,
        )

        return self._to_room_response(
            plan=plan,
            state=state,
            current_position_ms=request.start_at_ms,
        )

    async def get_room(
        self,
        room_id: UUID,
    ) -> ReplayRoomResponse:
        plan, state = await self._load_room(str(room_id))

        state = await self._complete_if_due(
            room_id=str(room_id),
            state=state,
        )

        current_position_ms = self._current_position(state)

        await self._touch_room(str(room_id))

        return self._to_room_response(
            plan=plan,
            state=state,
            current_position_ms=current_position_ms,
        )

    async def get_snapshot(
        self,
        room_id: UUID,
    ) -> ReplayRoomSnapshotResponse:
        plan, state = await self._load_room(str(room_id))

        state = await self._complete_if_due(
            room_id=str(room_id),
            state=state,
        )

        current_position_ms = self._current_position(state)

        await self._touch_room(str(room_id))

        return self._project_snapshot(
            room_id=room_id,
            plan=plan,
            state=state,
            current_position_ms=current_position_ms,
        )

    async def command(
        self,
        room_id: UUID,
        request: ReplayRoomCommandRequest,
    ) -> ReplayRoomResponse:
        room_id_text = str(room_id)

        plan, _ = await self._load_room(room_id_text)

        duration_ms = int(plan["duration_ms"])
        action = request.action

        if action == "SEEK":
            if request.source_cursor_ms is None:
                raise ReplayRoomInvalidRequestError(
                    "SEEK requires source_cursor_ms."
                )

            if request.source_cursor_ms > duration_ms:
                raise ReplayRoomInvalidRequestError(
                    "source_cursor_ms cannot exceed room duration."
                )

        if action == "SET_SPEED" and request.playback_speed is None:
            raise ReplayRoomInvalidRequestError(
                "SET_SPEED requires playback_speed."
            )

        def transition(
                state: _RoomState,
                current_position_ms: int,
                now_ms: int,
        ) -> _RoomState:
            if action == "PLAY":
                reset_replay = state.status in {
                    "STOPPED",
                    "COMPLETED",
                }

                position_ms = (
                    0
                    if reset_replay
                    else current_position_ms
                )

                return self._updated_state(
                    state,
                    now_ms,
                    status="PLAYING",
                    anchor_position_ms=position_ms,
                    anchor_epoch_ms=now_ms,
                    cursor_epoch=(
                        state.cursor_epoch + 1
                        if reset_replay
                        else state.cursor_epoch
                    ),
                )

            if action == "PAUSE":
                next_status = (
                    "COMPLETED"
                    if current_position_ms >= state.duration_ms
                    else "PAUSED"
                )

                return self._updated_state(
                    state,
                    now_ms,
                    status=next_status,
                    anchor_position_ms=current_position_ms,
                    anchor_epoch_ms=0,
                )

            if action == "SEEK":
                target_position_ms = request.source_cursor_ms or 0

                if target_position_ms >= state.duration_ms:
                    next_status = "COMPLETED"
                elif state.status == "PLAYING":
                    next_status = "PLAYING"
                else:
                    next_status = "PAUSED"

                return self._updated_state(
                    state,
                    now_ms,
                    status=next_status,
                    anchor_position_ms=target_position_ms,
                    anchor_epoch_ms=(
                        now_ms
                        if next_status == "PLAYING"
                        else 0
                    ),
                    cursor_epoch=state.cursor_epoch + 1,
                )

            if action == "SET_SPEED":
                return self._updated_state(
                    state,
                    now_ms,
                    playback_speed=request.playback_speed or 1.0,
                    anchor_position_ms=current_position_ms,
                    anchor_epoch_ms=(
                        now_ms
                        if state.status == "PLAYING"
                        else 0
                    ),
                )

            if action == "STOP":
                return self._updated_state(
                    state,
                    now_ms,
                    status="STOPPED",
                    anchor_position_ms=0,
                    anchor_epoch_ms=0,
                    cursor_epoch=state.cursor_epoch + 1,
                )

            raise ReplayRoomInvalidRequestError(
                f"Unsupported room action: {action}."
            )

    async def _load_room(
        self,
        room_id: str,
    ) -> tuple[dict[str, Any], _RoomState]:
        plan_text, state_values = await asyncio.gather(
            self.redis.get(self._plan_key(room_id)),
            self.redis.hgetall(self._state_key(room_id)),
        )

        if plan_text is None or not state_values:
            raise ReplayRoomNotFoundError("Replay room was not found.")

        return (
            json.loads(plan_text),
            _RoomState.from_mapping(state_values),
        )

    async def _complete_if_due(
        self,
        *,
        room_id: str,
        state: _RoomState,
    ) -> _RoomState:
        if (
            state.status != "PLAYING"
            or self._current_position(state) < state.duration_ms
        ):
            return state

        def complete(
            current_state: _RoomState,
            current_position_ms: int,
            now_ms: int,
        ) -> _RoomState:
            if (
                current_state.status != "PLAYING"
                or current_position_ms < current_state.duration_ms
            ):
                return current_state

            return self._updated_state(
                current_state,
                now_ms,
                status="COMPLETED",
                anchor_position_ms=current_state.duration_ms,
                anchor_epoch_ms=0,
            )

        return await self._mutate_state(
            room_id=room_id,
            expected_revision=None,
            transition=complete,
        )

    async def _mutate_state(
        self,
        *,
        room_id: str,
        expected_revision: int | None,
        transition: Callable[
            [_RoomState, int, int],
            _RoomState,
        ],
    ) -> _RoomState:
        state_key = self._state_key(room_id)

        while True:
            async with self.redis.pipeline() as pipeline:
                try:
                    await pipeline.watch(state_key)

                    values = await pipeline.hgetall(state_key)

                    if not values:
                        raise ReplayRoomNotFoundError(
                            "Replay room was not found."
                        )

                    state = _RoomState.from_mapping(values)

                    current_position_ms = self._current_position(state)
                    now_ms = self._now_ms()

                    if (
                        expected_revision is not None
                        and state.revision != expected_revision
                    ):
                        raise ReplayRoomRevisionConflict(
                            state.revision
                        )

                    next_state = transition(
                        state,
                        current_position_ms,
                        now_ms,
                    )

                    if next_state == state:
                        return state

                    pipeline.multi()

                    pipeline.hset(
                        state_key,
                        mapping=next_state.to_mapping(),
                    )

                    pipeline.expire(
                        state_key,
                        ROOM_TTL_SECONDS,
                    )

                    pipeline.expire(
                        self._plan_key(room_id),
                        ROOM_TTL_SECONDS,
                    )

                    await pipeline.execute()

                    return next_state

                except WatchError:
                    continue

    async def _touch_room(self, room_id: str) -> None:
        await self.redis.expire(
            self._state_key(room_id),
            ROOM_TTL_SECONDS,
        )

        await self.redis.expire(
            self._plan_key(room_id),
            ROOM_TTL_SECONDS,
        )

    @staticmethod
    def _updated_state(
        state: _RoomState,
        now_ms: int,
        **changes: object,
    ) -> _RoomState:
        return replace(
            state,
            revision=state.revision + 1,
            updated_at_epoch_ms=now_ms,
            **changes,
        )

    @staticmethod
    def _current_position(state: _RoomState) -> int:
        if state.status != "PLAYING":
            return state.anchor_position_ms

        elapsed_wall_clock_ms = max(
            0,
            ReplayRoomService._now_ms()
            - state.anchor_epoch_ms,
        )

        position_ms = state.anchor_position_ms + int(
            elapsed_wall_clock_ms * state.playback_speed
        )

        return min(position_ms, state.duration_ms)

    def _to_room_response(
            self,
            *,
            plan: dict[str, Any],
            state: _RoomState,
            current_position_ms: int,
    ) -> ReplayRoomResponse:
        map_metadata = plan.get("map", {})

        map_import_id = map_metadata.get("map_import_id")

        return ReplayRoomResponse(
            room_id=UUID(state.room_id),
            race_session_id=UUID(plan["race_session_id"]),
            session_name=plan["session_name"],
            session_type=plan["session_type"],
            status=state.status,
            playback_speed=state.playback_speed,
            source_cursor_ms=current_position_ms,
            duration_ms=state.duration_ms,
            timing_duration_ms=int(
                plan.get("timing_duration_ms", state.duration_ms)
            ),
            revision=state.revision,
            cursor_epoch=state.cursor_epoch,
            source_time_origin_ms=int(
                plan.get("source_time_origin_ms", 0)
            ),
            timing_tower_driver_count=len(
                plan["initial_tower"]
            ),
            replay_kind=plan["replay_kind"],
            track_map_available=bool(
                map_metadata.get("track_map_available", False)
            ),
            full_session_track_map_available=bool(
                map_metadata.get(
                    "full_session_track_map_available",
                    False,
                )
            ),
            map_import_id=(
                UUID(map_import_id)
                if map_import_id is not None
                else None
            ),
            map_driver_count=int(
                map_metadata.get("map_driver_count", 0)
            ),
            map_sample_interval_ms=map_metadata.get(
                "map_sample_interval_ms"
            ),
            map_time_alignment=map_metadata.get(
                "map_time_alignment"
            ),
            context_available=bool(
                plan.get("context_available", False)
            ),
            context_alignment=plan.get(
                "context_alignment",
                "NOT_IMPORTED",
            ),
            frozen_context_event_count=len(
                plan.get("context_events", [])
            ),
            websocket_path=(
                f"{settings.api_v1_prefix}/replay/rooms/"
                f"{state.room_id}/stream"
            ),
            created_at_epoch_ms=state.created_at_epoch_ms,
            updated_at_epoch_ms=state.updated_at_epoch_ms,
            warnings=plan["warnings"],
        )

    def _project_snapshot(
        self,
        *,
        room_id: UUID,
        plan: dict[str, Any],
        state: _RoomState,
        current_position_ms: int,
    ) -> ReplayRoomSnapshotResponse:
        tower = {
            row["driver_number"]: dict(row)
            for row in plan["initial_tower"]
        }

        applied_event_count = 0

        for event in plan["events"]:
            if event["at_ms"] > current_position_ms:
                break

            row = tower[event["driver_number"]]
            event_type = event["event_type"]

            if event_type == "lap.completed":
                row["status"] = "ACTIVE"
                row["completed_laps"] = max(
                    row["completed_laps"],
                    event["lap_number"],
                )
                row["current_lap_number"] = (
                    event["lap_number"] + 1
                )

                row["last_lap_time_ms"] = event["lap_time_ms"]

                best_lap_time_ms = row["best_lap_time_ms"]

                if (
                    best_lap_time_ms is None
                    or event["lap_time_ms"] < best_lap_time_ms
                ):
                    row["best_lap_time_ms"] = event[
                        "lap_time_ms"
                    ]

                row["sector_1_time_ms"] = event[
                    "sector_1_time_ms"
                ]
                row["sector_2_time_ms"] = event[
                    "sector_2_time_ms"
                ]
                row["sector_3_time_ms"] = event[
                    "sector_3_time_ms"
                ]

                row["stint"] = event["stint"]
                row["compound"] = event["compound"]
                row["tyre_life"] = event["tyre_life"]

                row["track_status_raw"] = event[
                    "track_status_raw"
                ]

                row["in_pit_lane"] = (
                    event["pit_in"]
                    and not event["pit_out"]
                )

                if event["position"] is not None:
                    row["track_position"] = event["position"]

                row["last_lap_quality_flags"] = event[
                    "quality_flags"
                ]

            elif event_type == "driver.finished":
                row["status"] = event["final_status"] or "FINISHED"
                row["in_pit_lane"] = False

            applied_event_count += 1

        leader_completed_laps = max(
            row["completed_laps"]
            for row in tower.values()
        )

        rows = []

        for row in tower.values():
            row["laps_down"] = max(
                0,
                leader_completed_laps - row["completed_laps"],
            )

            rows.append(TimingTowerRowResponse(**row))

        rows.sort(
            key=lambda row: (
                row.track_position is None,
                row.track_position or 999,
                self._driver_sort_key(row.driver_number),
            )
        )

        return ReplayRoomSnapshotResponse(
            room_id=room_id,
            status=state.status,
            revision=state.revision,
            source_cursor_ms=current_position_ms,
            duration_ms=state.duration_ms,
            progress_percentage=round(
                current_position_ms
                / state.duration_ms
                * 100,
                3,
            ),
            applied_event_count=applied_event_count,
            timing_semantics=TIMING_SEMANTICS,
            rows=rows,
            warnings=plan["warnings"],
            cursor_epoch=state.cursor_epoch,
            context_state=self._project_context_state(
                plan=plan,
                source_cursor_ms=current_position_ms,
            ),
        )


    @staticmethod
    def _context_events_between(
        *,
        plan: dict[str, Any],
        after_cursor_ms: int,
        through_cursor_ms: int,
    ) -> list[ReplayRoomContextEventResponse]:
        if through_cursor_ms <= after_cursor_ms:
            return []

        return [
            ReplayRoomContextEventResponse(
                event_id=event["event_id"],
                event_type=event["event_type"],
                source_cursor_ms=int(event["at_ms"]),
                source_session_time_ms=int(
                    event["source_session_time_ms"]
                ),
                occurred_at=event.get("occurred_at"),
                priority=int(event["priority"]),
                title=event["title"],
                message=event["message"],
                severity=event["severity"],
                driver_number=event.get("driver_number"),
                lap_number=event.get("lap_number"),
                data_quality_flags=event.get(
                    "data_quality_flags",
                    [],
                ),
                payload=event.get("payload", {}),
            )
            for event in plan.get("context_events", [])
            if (
                after_cursor_ms
                < int(event["at_ms"])
                <= through_cursor_ms
            )
        ]

    @staticmethod
    def _project_context_state(
        *,
        plan: dict[str, Any],
        source_cursor_ms: int,
    ) -> ReplayRoomContextStateResponse:
        passed_event_count = 0
        latest_weather: ReplayRoomContextEventResponse | None = None

        for event in plan.get("context_events", []):
            if int(event["at_ms"]) > source_cursor_ms:
                break

            passed_event_count += 1

            if event["event_type"] == "WEATHER":
                latest_weather = ReplayRoomContextEventResponse(
                    event_id=event["event_id"],
                    event_type=event["event_type"],
                    source_cursor_ms=int(event["at_ms"]),
                    source_session_time_ms=int(
                        event["source_session_time_ms"]
                    ),
                    occurred_at=event.get("occurred_at"),
                    priority=int(event["priority"]),
                    title=event["title"],
                    message=event["message"],
                    severity=event["severity"],
                    driver_number=event.get("driver_number"),
                    lap_number=event.get("lap_number"),
                    data_quality_flags=event.get(
                        "data_quality_flags",
                        [],
                    ),
                    payload=event.get("payload", {}),
                )

        return ReplayRoomContextStateResponse(
            passed_event_count=passed_event_count,
            latest_weather=latest_weather,
        )

    @staticmethod
    def _build_session_plan(
        race_session_id: UUID,
    ) -> dict[str, Any]:
        db = SessionLocal()

        try:
            race_session = db.get(
                RaceSession,
                race_session_id,
            )

            if race_session is None:
                raise ReplaySourceSessionNotFoundError(
                    "Race session was not found."
                )

            if race_session.session_type.casefold() != "race":
                raise NonRaceReplaySessionError(
                    "Full replay rooms currently support Race sessions only."
                )

            result_rows = db.execute(
                select(SessionResult, Driver, Team)
                .join(
                    Driver,
                    SessionResult.driver_id == Driver.id,
                )
                .outerjoin(
                    Team,
                    Team.id == SessionResult.team_id,
                )
                .where(
                    SessionResult.race_session_id
                    == race_session_id
                )
            ).all()

            if not result_rows:
                raise ReplayRoomInvalidRequestError(
                    "This session has no imported driver results."
                )

            initial_tower: list[dict[str, Any]] = []

            for result, driver, team in result_rows:
                grid_position = (
                    result.grid_position
                    if result.grid_position
                    and result.grid_position > 0
                    else None
                )

                initial_tower.append(
                    {
                        "driver_number": driver.driver_number,
                        "abbreviation": driver.abbreviation,
                        "driver_name": (
                            ReplayRoomService._driver_name(driver)
                        ),
                        "team_name": (
                            team.name if team else None
                        ),
                        "team_colour": (
                            team.colour if team else None
                        ),
                        "grid_position": grid_position,
                        "track_position": grid_position,
                        "completed_laps": 0,
                        "current_lap_number": 1,
                        "last_lap_time_ms": None,
                        "best_lap_time_ms": None,
                        "sector_1_time_ms": None,
                        "sector_2_time_ms": None,
                        "sector_3_time_ms": None,
                        "stint": None,
                        "compound": None,
                        "tyre_life": None,
                        "track_status_raw": None,
                        "in_pit_lane": False,
                        "laps_down": 0,
                        "status": "READY",
                        "final_status": result.status,
                        "last_lap_quality_flags": [],
                    }
                )

            initial_tower.sort(
                key=lambda row: (
                    row["grid_position"] is None,
                    row["grid_position"] or 999,
                    ReplayRoomService._driver_sort_key(
                        row["driver_number"]
                    ),
                )
            )

            lap_rows = db.execute(
                select(Lap, Driver)
                .join(Driver, Lap.driver_id == Driver.id)
                .where(
                    Lap.race_session_id == race_session_id
                )
                .order_by(
                    Driver.driver_number,
                    Lap.lap_number,
                )
            ).all()

            laps_by_driver: dict[
                str,
                list[tuple[Lap, Driver]],
            ] = defaultdict(list)

            for lap, driver in lap_rows:
                laps_by_driver[driver.driver_number].append(
                    (lap, driver)
                )

            prepared_laps: list[dict[str, Any]] = []
            warnings = [TIMING_SEMANTICS]

            corrected_first_lap_markers = 0
            derived_clock_entries = 0
            missing_lap_times = 0

            for driver_number, entries in laps_by_driver.items():
                previous_end_ms: int | None = None

                for index, (lap, driver) in enumerate(entries):
                    original_start_ms = lap.lap_start_time_ms
                    effective_start_ms = original_start_ms
                    adjustment_ms = 0

                    next_lap = (
                        entries[index + 1][0]
                        if index + 1 < len(entries)
                        else None
                    )

                    if (
                        lap.lap_number == 1
                        and lap.lap_time_ms is not None
                        and original_start_ms is not None
                        and next_lap is not None
                        and next_lap.lap_start_time_ms is not None
                    ):
                        corrected_start_ms = (
                            next_lap.lap_start_time_ms
                            - lap.lap_time_ms
                        )

                        if (
                            abs(
                                corrected_start_ms
                                - original_start_ms
                            )
                            <= 1_000
                        ):
                            effective_start_ms = corrected_start_ms
                            adjustment_ms = (
                                corrected_start_ms
                                - original_start_ms
                            )
                            corrected_first_lap_markers += 1

                    if effective_start_ms is None:
                        if previous_end_ms is not None:
                            effective_start_ms = previous_end_ms
                            derived_clock_entries += 1

                    if effective_start_ms is None:
                        continue

                    if lap.lap_time_ms is None:
                        missing_lap_times += 1
                    else:
                        previous_end_ms = (
                            effective_start_ms
                            + lap.lap_time_ms
                        )

                    prepared_laps.append(
                        {
                            "lap": lap,
                            "driver_number": driver_number,
                            "effective_start_ms": effective_start_ms,
                            "source_start_ms": original_start_ms,
                            "adjustment_ms": adjustment_ms,
                        }
                    )

            timed_laps = [
                item
                for item in prepared_laps
                if item["lap"].lap_time_ms is not None
            ]

            if not timed_laps:
                raise ReplayRoomInvalidRequestError(
                    "This session has no replayable lap timing data."
                )

            source_time_origin_ms = min(
                item["effective_start_ms"]
                for item in timed_laps
            )

            events: list[dict[str, Any]] = []

            for item in timed_laps:
                lap: Lap = item["lap"]

                lap_end_ms = (
                    item["effective_start_ms"]
                    - source_time_origin_ms
                    + lap.lap_time_ms
                )

                events.append(
                    {
                        "event_type": "lap.completed",
                        "at_ms": max(0, lap_end_ms),
                        "driver_number": item["driver_number"],
                        "lap_id": str(lap.id),
                        "lap_number": lap.lap_number,
                        "lap_time_ms": lap.lap_time_ms,
                        "sector_1_time_ms": lap.sector_1_time_ms,
                        "sector_2_time_ms": lap.sector_2_time_ms,
                        "sector_3_time_ms": lap.sector_3_time_ms,
                        "position": lap.position,
                        "stint": lap.stint,
                        "compound": lap.compound,
                        "tyre_life": (
                            float(lap.tyre_life)
                            if lap.tyre_life is not None
                            else None
                        ),
                        "track_status_raw": lap.track_status,
                        "pit_in": lap.pit_in_time_ms is not None,
                        "pit_out": lap.pit_out_time_ms is not None,
                        "quality_flags": (
                            ReplayRoomService._lap_quality_flags(lap)
                        ),
                        "source_lap_start_ms": (
                            item["source_start_ms"]
                        ),
                        "effective_lap_start_ms": (
                            item["effective_start_ms"]
                        ),
                        "timing_boundary_adjustment_ms": (
                            item["adjustment_ms"]
                        ),
                    }
                )

            timing_duration_ms = max(
                event["at_ms"]
                for event in events
            )

            enrichment = ReplayRoomPlanService(
                db
            ).build_enrichment(
                race_session_id=race_session_id,
                source_time_origin_ms=source_time_origin_ms,
                timing_duration_ms=timing_duration_ms,
                eligible_driver_count=len(initial_tower),
            )

            duration_ms = timing_duration_ms

            for row in initial_tower:
                events.append(
                    {
                        "event_type": "driver.finished",
                        "at_ms": timing_duration_ms,
                        "driver_number": row["driver_number"],
                        "final_status": row["final_status"],
                    }
                )

            event_order = {
                "lap.completed": 0,
                "driver.finished": 1,
            }

            events.sort(
                key=lambda event: (
                    event["at_ms"],
                    event_order[event["event_type"]],
                    ReplayRoomService._driver_sort_key(
                        event["driver_number"]
                    ),
                )
            )

            for event_index, event in enumerate(events):
                event["event_index"] = event_index

            if corrected_first_lap_markers:
                warnings.append(
                    "FIRST_LAP_START_MARKERS_CORRECTED"
                )

            if derived_clock_entries:
                warnings.append("DERIVED_SESSION_CLOCK")

            if missing_lap_times:
                warnings.append(
                    f"MISSING_LAP_TIMES_SKIPPED:"
                    f"{missing_lap_times}"
                )

            warnings.extend(enrichment.warnings)

            if not enrichment.map_metadata[
                "track_map_available"
            ]:
                warnings.append(
                    "TRACK_MAP_REQUIRES_BATCH_TELEMETRY_IMPORT"
                )

            return {
                "plan_version": "session-timing-room-v2",
                "race_session_id": str(race_session.id),
                "session_name": race_session.name,
                "session_type": race_session.session_type,
                "duration_ms": duration_ms,
                "timing_duration_ms": timing_duration_ms,
                "source_time_origin_ms": source_time_origin_ms,
                "replay_kind": REPLAY_KIND,
                "initial_tower": initial_tower,
                "events": events,
                "map": enrichment.map_metadata,
                "context_available": (
                    enrichment.context_available
                ),
                "context_alignment": (
                    enrichment.context_alignment
                ),
                "context_events": enrichment.context_events,
                "warnings": warnings,
            }

        finally:
            db.close()

    @staticmethod
    def _lap_quality_flags(lap: Lap) -> list[str]:
        flags: list[str] = []

        if lap.is_accurate is not True:
            flags.append("INACCURATE_LAP")

        if lap.deleted is True or lap.deleted_reason is not None:
            flags.append("DELETED_LAP")

        if lap.fastf1_generated is True:
            flags.append("FASTF1_GENERATED")

        if lap.track_status != "1":
            flags.append("NON_GREEN_TRACK_STATUS")

        return flags

    @staticmethod
    def _driver_name(driver: Driver) -> str:
        return (
            driver.full_name
            or " ".join(
                part
                for part in [
                    driver.first_name,
                    driver.last_name,
                ]
                if part
            )
            or driver.abbreviation
            or driver.driver_number
        )

    @staticmethod
    def _driver_sort_key(driver_number: str) -> tuple[int, str]:
        if driver_number.isdigit():
            return int(driver_number), driver_number

        return 999, driver_number

    @staticmethod
    def _plan_key(room_id: str) -> str:
        return f"racepulse:replay-room:{room_id}:plan"

    @staticmethod
    def _state_key(room_id: str) -> str:
        return f"racepulse:replay-room:{room_id}:state"

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)