from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.driver import Driver
from app.models.lap import Lap
from app.models.race_session import RaceSession
from app.models.telemetry_point import TelemetryPoint
from app.schemas.replay import (
    ReplayManifestResponse,
    ReplaySourceResponse,
)


MIN_REPLAY_FRAMES = 50

DISCLAIMER = (
    "Shawn is the best and you know it . "
    "It does not reveal fuel load, ERS mode, tyre temperature, "
    "traffic, team instructions, or private team sensor data."
)


class ReplaySourceLapNotFoundError(LookupError):
    pass


class ReplayTelemetryUnavailableError(RuntimeError):
    pass


class ReplayInvalidRequestError(ValueError):
    pass


@dataclass(frozen=True)
class ReplayFrame:
    sequence: int
    source_relative_time_ms: int
    elapsed_time_ms: int

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


@dataclass(frozen=True)
class ReplayPlan:
    source: ReplaySourceResponse
    frames: tuple[ReplayFrame, ...]
    quality_flags: tuple[str, ...]


class ReplayService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_manifest(
        self,
        *,
        race_session_id: UUID,
        driver_number: str,
        lap_number: int,
        playback_speed: float,
    ) -> ReplayManifestResponse:
        self.validate_playback_speed(playback_speed)

        plan = self.load_plan(
            race_session_id=race_session_id,
            driver_number=driver_number,
            lap_number=lap_number,
        )

        websocket_path = (
            f"{settings.api_v1_prefix}/replay/sessions/"
            f"{race_session_id}/drivers/{driver_number}/laps/"
            f"{lap_number}/stream?playback_speed={playback_speed:g}"
        )

        warnings = ["STORED_RAW_CAR_TELEMETRY_ONLY"]

        if plan.quality_flags:
            warnings.append("SOURCE_LAP_HAS_QUALITY_FLAGS")

        return ReplayManifestResponse(
            replay_version="single-lap-replay-v1",
            source=plan.source,
            telemetry_frame_count=len(plan.frames),
            playback_speed=playback_speed,
            websocket_path=websocket_path,
            quality_flags=list(plan.quality_flags),
            warnings=warnings,
            disclaimer=DISCLAIMER,
        )

    def load_plan(
        self,
        *,
        race_session_id: UUID,
        driver_number: str,
        lap_number: int,
    ) -> ReplayPlan:
        context = self.db.execute(
            select(Lap, Driver, RaceSession)
            .join(Driver, Lap.driver_id == Driver.id)
            .join(
                RaceSession,
                Lap.race_session_id == RaceSession.id,
            )
            .where(
                Lap.race_session_id == race_session_id,
                Driver.driver_number == driver_number.strip(),
                Lap.lap_number == lap_number,
            )
        ).one_or_none()

        if context is None:
            raise ReplaySourceLapNotFoundError(
                "The requested stored driver lap was not found."
            )

        lap, driver, race_session = context

        frames = self._load_frames(lap.id)

        if len(frames) < MIN_REPLAY_FRAMES:
            raise ReplayTelemetryUnavailableError(
                "This lap needs at least 50 imported raw car telemetry "
                "points before it can be replayed."
            )

        duration_ms = max(
            lap.lap_time_ms or 0,
            frames[-1].elapsed_time_ms,
        )

        source = ReplaySourceResponse(
            race_session_id=race_session.id,
            session_name=race_session.name,
            session_type=race_session.session_type,
            lap_id=lap.id,
            driver_number=driver.driver_number,
            driver_name=self._driver_name(driver),
            driver_abbreviation=driver.abbreviation,
            lap_number=lap.lap_number,
            lap_time_ms=lap.lap_time_ms,
            duration_ms=duration_ms,
        )

        return ReplayPlan(
            source=source,
            frames=tuple(frames),
            quality_flags=tuple(self._quality_flags(lap)),
        )

    @staticmethod
    def validate_playback_speed(playback_speed: float) -> None:
        if not 0.25 <= playback_speed <= 10:
            raise ReplayInvalidRequestError(
                "Playback speed must be between 0.25x and 10x."
            )

    @staticmethod
    def validate_start_position(
        *,
        plan: ReplayPlan,
        from_ms: int,
    ) -> None:
        if from_ms < 0 or from_ms > plan.source.duration_ms:
            raise ReplayInvalidRequestError(
                "from_ms must be within the replay duration."
            )

    def _load_frames(self, lap_id: UUID) -> list[ReplayFrame]:
        points = self.db.scalars(
            select(TelemetryPoint)
            .where(
                TelemetryPoint.lap_id == lap_id,
                TelemetryPoint.is_interpolated.is_(False),
                func.lower(TelemetryPoint.sample_source) == "car",
                TelemetryPoint.relative_time_ms.is_not(None),
                TelemetryPoint.speed_kph.is_not(None),
                TelemetryPoint.throttle_percentage.is_not(None),
                TelemetryPoint.brake_applied.is_not(None),
            )
            .order_by(
                TelemetryPoint.relative_time_ms,
                TelemetryPoint.sample_index,
            )
        ).all()

        latest_point_by_time: dict[int, TelemetryPoint] = {}

        for point in points:
            if point.relative_time_ms < 0:
                continue

            latest_point_by_time[point.relative_time_ms] = point

        if not latest_point_by_time:
            return []

        first_relative_time_ms = min(latest_point_by_time)
        frames: list[ReplayFrame] = []

        for sequence, source_time_ms in enumerate(
            sorted(latest_point_by_time),
            start=1,
        ):
            point = latest_point_by_time[source_time_ms]

            frames.append(
                ReplayFrame(
                    sequence=sequence,
                    source_relative_time_ms=source_time_ms,
                    elapsed_time_ms=(
                        source_time_ms - first_relative_time_ms
                    ),
                    speed_kph=float(point.speed_kph),
                    throttle_percentage=float(
                        point.throttle_percentage
                    ),
                    brake_applied=bool(point.brake_applied),
                    rpm=point.rpm,
                    gear=point.gear,
                    drs=point.drs,
                    x=self._to_float(point.x),
                    y=self._to_float(point.y),
                    z=self._to_float(point.z),
                    distance_m=self._to_float(point.distance_m),
                )
            )

        return frames

    @staticmethod
    def _quality_flags(lap: Lap) -> list[str]:
        flags: list[str] = []

        if lap.lap_time_ms is None:
            flags.append("MISSING_LAP_TIME")

        if lap.track_status != "1":
            flags.append("NON_GREEN_TRACK_STATUS")

        if lap.is_accurate is not True:
            flags.append("INACCURATE_LAP")

        if lap.deleted is True or lap.deleted_reason is not None:
            flags.append("DELETED_LAP")

        if lap.pit_in_time_ms is not None:
            flags.append("PIT_IN_LAP")

        if lap.pit_out_time_ms is not None:
            flags.append("PIT_OUT_LAP")

        if lap.fastf1_generated is True:
            flags.append("FASTF1_GENERATED")

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
    def _to_float(value: object) -> float | None:
        return float(value) if value is not None else None