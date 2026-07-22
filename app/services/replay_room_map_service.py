import asyncio
from uuid import UUID, uuid4

from redis.asyncio import Redis
from sqlalchemy import func, select

from app.models.session_map_import import (
    SessionMapDriverStatus,
    SessionMapImport,
    SessionMapImportStatus,
)
from app.models.session_map_import_driver import (
    SessionMapImportDriver,
)

from app.db.database import SessionLocal
from app.models.driver import Driver
from app.models.session_map_import import SessionMapDriverStatus
from app.models.session_map_import_driver import (
    SessionMapImportDriver,
)
from app.models.session_map_sample import SessionMapSample
from app.schemas.replay_map import (
    ReplayMapDriverPositionResponse,
    ReplayMapFrameResponse,
)
from app.schemas.replay_room import ReplayRoomResponse


MAP_FRAME_CACHE_SECONDS = 10
MAP_FRAME_LOCK_MS = 2_000


class ReplayRoomMapService:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def get_frame(
        self,
        *,
        room: ReplayRoomResponse,
        source_cursor_ms: int,
    ) -> ReplayMapFrameResponse | None:
        if (
            not room.track_map_available
            or room.map_import_id is None
            or room.map_sample_interval_ms is None
        ):
            return None

        interval_ms = room.map_sample_interval_ms

        bucket_cursor_ms = min(
            room.duration_ms,
            max(0, source_cursor_ms)
            // interval_ms
            * interval_ms,
        )

        cache_key = (
            f"racepulse:replay-room:{room.room_id}:"
            f"map-frame:{room.map_import_id}:{bucket_cursor_ms}"
        )

        cached = await self.redis.get(cache_key)

        if cached is not None:
            return ReplayMapFrameResponse.model_validate_json(
                cached
            )

        lock_key = f"{cache_key}:lock"
        lock_token = str(uuid4())

        acquired = await self.redis.set(
            lock_key,
            lock_token,
            nx=True,
            px=MAP_FRAME_LOCK_MS,
        )

        if not acquired:
            for _ in range(5):
                await asyncio.sleep(0.02)

                cached = await self.redis.get(cache_key)

                if cached is not None:
                    return (
                        ReplayMapFrameResponse.model_validate_json(
                            cached
                        )
                    )

        try:
            frame = await asyncio.to_thread(
                self._load_frame,
                room,
                bucket_cursor_ms,
            )

            await self.redis.set(
                cache_key,
                frame.model_dump_json(),
                ex=MAP_FRAME_CACHE_SECONDS,
            )

            return frame

        finally:
            if acquired:
                await self._release_lock(
                    lock_key,
                    lock_token,
                )

    @staticmethod
    def _load_frame(
        room: ReplayRoomResponse,
        source_cursor_ms: int,
    ) -> ReplayMapFrameResponse:
        if (
            room.map_import_id is None
            or room.map_sample_interval_ms is None
        ):
            raise RuntimeError(
                "The replay room has no map dataset."
            )

        source_session_time_ms = (
            room.source_time_origin_ms
            + source_cursor_ms
        )

        db = SessionLocal()

        try:
            driver_rows = db.execute(
                select(SessionMapImportDriver, Driver)
                .join(
                    Driver,
                    SessionMapImportDriver.driver_id
                    == Driver.id,
                )
                .where(
                    SessionMapImportDriver.map_import_id
                    == room.map_import_id,
                    SessionMapImportDriver.status.in_(
                        [
                            SessionMapDriverStatus.READY,
                            SessionMapDriverStatus.PARTIAL,
                        ]
                    ),
                )
            ).all()

            latest_samples = db.scalars(
                select(SessionMapSample)
                .where(
                    SessionMapSample.map_import_id
                    == room.map_import_id,
                    SessionMapSample.session_time_ms
                    >= room.source_time_origin_ms,
                    SessionMapSample.session_time_ms
                    <= source_session_time_ms,
                )
                .distinct(SessionMapSample.driver_id)
                .order_by(
                    SessionMapSample.driver_id,
                    SessionMapSample.session_time_ms.desc(),
                )
            ).all()

            sample_by_driver = {
                sample.driver_id: sample
                for sample in latest_samples
            }

            stale_after_ms = max(
                1_000,
                room.map_sample_interval_ms * 4,
            )

            positions: list[
                ReplayMapDriverPositionResponse
            ] = []

            for item, driver in driver_rows:
                sample = sample_by_driver.get(driver.id)

                if sample is None:
                    if (
                        item.first_sample_session_time_ms
                        is not None
                        and source_session_time_ms
                        < item.first_sample_session_time_ms
                    ):
                        map_state = "NOT_STARTED"
                    else:
                        map_state = "UNAVAILABLE"

                    positions.append(
                        ReplayMapDriverPositionResponse(
                            driver_number=driver.driver_number,
                            abbreviation=driver.abbreviation,
                            driver_name=(
                                ReplayRoomMapService
                                ._driver_name(driver)
                            ),
                            import_status=item.status.value,
                            map_state=map_state,
                            sample_session_time_ms=None,
                            source_age_ms=None,
                            x=None,
                            y=None,
                            z=None,
                            position_status=None,
                        )
                    )

                    continue

                source_age_ms = max(
                    0,
                    source_session_time_ms
                    - sample.session_time_ms,
                )

                map_state = (
                    "LIVE"
                    if source_age_ms <= stale_after_ms
                    else "STALE"
                )

                positions.append(
                    ReplayMapDriverPositionResponse(
                        driver_number=driver.driver_number,
                        abbreviation=driver.abbreviation,
                        driver_name=(
                            ReplayRoomMapService
                            ._driver_name(driver)
                        ),
                        import_status=item.status.value,
                        map_state=map_state,
                        sample_session_time_ms=(
                            sample.session_time_ms
                        ),
                        source_age_ms=source_age_ms,
                        x=float(sample.x),
                        y=float(sample.y),
                        z=(
                            float(sample.z)
                            if sample.z is not None
                            else None
                        ),
                        position_status=sample.position_status,
                    )
                )

            positions.sort(
                key=lambda position: (
                    int(position.driver_number)
                    if position.driver_number.isdigit()
                    else 9999,
                    position.driver_number,
                )
            )

            return ReplayMapFrameResponse(
                map_import_id=room.map_import_id,
                race_session_id=room.race_session_id,
                source_time_origin_ms=(
                    room.source_time_origin_ms
                ),
                source_cursor_ms=source_cursor_ms,
                source_session_time_ms=(
                    source_session_time_ms
                ),
                sample_interval_ms=(
                    room.map_sample_interval_ms
                ),
                alignment=(
                    room.map_time_alignment
                    or (
                        "FASTF1_SESSION_TIME_MINUS_"
                        "PLAN_ORIGIN"
                    )
                ),
                drivers=positions,
            )

        finally:
            db.close()

    async def _release_lock(
        self,
        lock_key: str,
        lock_token: str,
    ) -> None:
        script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        end
        return 0
        """

        await self.redis.eval(
            script,
            1,
            lock_key,
            lock_token,
        )

    @staticmethod
    def _driver_name(driver: Driver) -> str:
        return (
            driver.full_name
            or " ".join(
                name
                for name in [
                    driver.first_name,
                    driver.last_name,
                ]
                if name
            )
            or driver.abbreviation
            or driver.driver_number
        )