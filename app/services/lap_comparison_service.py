from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.driver import Driver
from app.models.lap import Lap
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.team import Team
from app.models.telemetry_point import TelemetryPoint
from app.schemas.lap_comparison import (
    ComparedLapResponse,
    ComparisonSessionResponse,
    LapComparisonDeltaResponse,
    LapComparisonResponse,
    LapStintContextResponse,
    SectorTimesResponse,
    SpeedPointsDeltaResponse,
    SpeedPointsResponse,
    TelemetryComparisonReadinessResponse,
    TimingDeltaResponse,
)


MIN_RAW_TELEMETRY_SAMPLES = 50

DISCLAIMER = (
    "All delta fields use target minus reference. Negative timing "
    "deltas mean the target lap was faster; positive speed deltas mean "
    "the target was faster at that measurement point. This comparison "
    "cannot isolate fuel load, ERS mode, tyre temperature, traffic, "
    "track evolution, or team instructions."
)


class ComparisonSessionNotFoundError(LookupError):
    pass


class ComparisonLapNotFoundError(LookupError):
    pass


class CrossMeetingComparisonError(ValueError):
    pass


class MeaninglessLapComparisonError(ValueError):
    pass


class LapComparisonService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def compare(
        self,
        *,
        reference_session_id: UUID,
        reference_driver_number: str,
        reference_lap_number: int,
        target_session_id: UUID,
        target_driver_number: str,
        target_lap_number: int,
    ) -> LapComparisonResponse:
        reference_session = self._get_session(
            reference_session_id,
            "Reference",
        )
        target_session = self._get_session(
            target_session_id,
            "Target",
        )

        if reference_session.meeting_id != target_session.meeting_id:
            raise CrossMeetingComparisonError(
                "Both laps must belong to sessions from the same meeting."
            )

        reference_lap, reference_driver, reference_team = (
            self._get_lap_context(
                session_id=reference_session_id,
                driver_number=reference_driver_number,
                lap_number=reference_lap_number,
                label="Reference",
            )
        )

        target_lap, target_driver, target_team = self._get_lap_context(
            session_id=target_session_id,
            driver_number=target_driver_number,
            lap_number=target_lap_number,
            label="Target",
        )

        if reference_lap.id == target_lap.id:
            raise MeaninglessLapComparisonError(
                "Reference and target cannot be the same stored lap."
            )

        reference = self._to_lap_response(
            lap=reference_lap,
            driver=reference_driver,
            team=reference_team,
        )
        target = self._to_lap_response(
            lap=target_lap,
            driver=target_driver,
            team=target_team,
        )

        timing_delta = self._int_delta(
            target_lap.lap_time_ms,
            reference_lap.lap_time_ms,
        )

        telemetry = self._telemetry_readiness(
            reference_lap_id=reference_lap.id,
            target_lap_id=target_lap.id,
        )

        warnings = self._comparison_warnings(
            reference_session=reference_session,
            target_session=target_session,
            reference=reference,
            target=target,
        )

        return LapComparisonResponse(
            comparison_version="lap-comparison-v1",
            comparison_scope=(
                "SAME_SESSION"
                if reference_session.id == target_session.id
                else "SAME_MEETING_CROSS_SESSION"
            ),
            reference_session=self._to_session_response(
                reference_session
            ),
            target_session=self._to_session_response(target_session),
            reference=reference,
            target=target,
            delta=LapComparisonDeltaResponse(
                convention="TARGET_MINUS_REFERENCE",
                faster_driver_number_by_lap_time=(
                    self._faster_driver_number(
                        timing_delta=timing_delta,
                        reference_driver_number=(
                            reference_driver.driver_number
                        ),
                        target_driver_number=target_driver.driver_number,
                    )
                ),
                timing=TimingDeltaResponse(
                    lap_time_ms=timing_delta,
                    sector_1_ms=self._int_delta(
                        target_lap.sector_1_time_ms,
                        reference_lap.sector_1_time_ms,
                    ),
                    sector_2_ms=self._int_delta(
                        target_lap.sector_2_time_ms,
                        reference_lap.sector_2_time_ms,
                    ),
                    sector_3_ms=self._int_delta(
                        target_lap.sector_3_time_ms,
                        reference_lap.sector_3_time_ms,
                    ),
                ),
                speed_points=SpeedPointsDeltaResponse(
                    speed_i1_kph=self._float_delta(
                        target_lap.speed_i1,
                        reference_lap.speed_i1,
                    ),
                    speed_i2_kph=self._float_delta(
                        target_lap.speed_i2,
                        reference_lap.speed_i2,
                    ),
                    finish_line_speed_kph=self._float_delta(
                        target_lap.speed_fl,
                        reference_lap.speed_fl,
                    ),
                    speed_trap_kph=self._float_delta(
                        target_lap.speed_st,
                        reference_lap.speed_st,
                    ),
                ),
            ),
            telemetry=telemetry,
            comparison_warnings=warnings,
            disclaimer=DISCLAIMER,
        )

    def _get_session(
        self,
        session_id: UUID,
        label: str,
    ) -> RaceSession:
        session = self.db.get(RaceSession, session_id)

        if session is None:
            raise ComparisonSessionNotFoundError(
                f"{label} session was not found."
            )

        return session

    def _get_lap_context(
        self,
        *,
        session_id: UUID,
        driver_number: str,
        lap_number: int,
        label: str,
    ) -> tuple[Lap, Driver, Team | None]:
        row = self.db.execute(
            select(Lap, Driver, Team)
            .join(Driver, Lap.driver_id == Driver.id)
            .outerjoin(
                SessionResult,
                and_(
                    SessionResult.race_session_id
                    == Lap.race_session_id,
                    SessionResult.driver_id == Lap.driver_id,
                ),
            )
            .outerjoin(Team, Team.id == SessionResult.team_id)
            .where(
                Lap.race_session_id == session_id,
                Driver.driver_number == driver_number.strip(),
                Lap.lap_number == lap_number,
            )
        ).one_or_none()

        if row is None:
            raise ComparisonLapNotFoundError(
                f"{label} driver lap was not found in the selected session."
            )

        return row

    @staticmethod
    def _to_session_response(
        session: RaceSession,
    ) -> ComparisonSessionResponse:
        return ComparisonSessionResponse(
            id=session.id,
            name=session.name,
            session_identifier=session.session_identifier,
            session_type=session.session_type,
        )

    def _to_lap_response(
        self,
        *,
        lap: Lap,
        driver: Driver,
        team: Team | None,
    ) -> ComparedLapResponse:
        return ComparedLapResponse(
            lap_id=lap.id,
            driver_number=driver.driver_number,
            abbreviation=driver.abbreviation,
            driver_name=self._driver_name(driver),
            team_name=team.name if team else None,
            team_colour=team.colour if team else None,
            lap_number=lap.lap_number,
            lap_time_ms=lap.lap_time_ms,
            sectors=SectorTimesResponse(
                sector_1_ms=lap.sector_1_time_ms,
                sector_2_ms=lap.sector_2_time_ms,
                sector_3_ms=lap.sector_3_time_ms,
            ),
            speed_points=SpeedPointsResponse(
                speed_i1_kph=self._to_float(lap.speed_i1),
                speed_i2_kph=self._to_float(lap.speed_i2),
                finish_line_speed_kph=self._to_float(lap.speed_fl),
                speed_trap_kph=self._to_float(lap.speed_st),
            ),
            stint_context=LapStintContextResponse(
                stint_number=lap.stint,
                compound=lap.compound,
                tyre_life=self._to_float(lap.tyre_life),
                fresh_tyre=lap.fresh_tyre,
                position=lap.position,
                track_status=lap.track_status,
            ),
            quality_flags=self._quality_flags(lap),
        )

    def _telemetry_readiness(
        self,
        *,
        reference_lap_id: UUID,
        target_lap_id: UUID,
    ) -> TelemetryComparisonReadinessResponse:
        reference_count = self._raw_telemetry_count(reference_lap_id)
        target_count = self._raw_telemetry_count(target_lap_id)

        missing_reasons: list[str] = []

        if reference_count < MIN_RAW_TELEMETRY_SAMPLES:
            missing_reasons.append(
                "REFERENCE_TELEMETRY_NOT_READY"
            )

        if target_count < MIN_RAW_TELEMETRY_SAMPLES:
            missing_reasons.append(
                "TARGET_TELEMETRY_NOT_READY"
            )

        return TelemetryComparisonReadinessResponse(
            telemetry_ready=not missing_reasons,
            reference_raw_sample_count=reference_count,
            target_raw_sample_count=target_count,
            missing_reasons=missing_reasons,
        )

    def _raw_telemetry_count(self, lap_id: UUID) -> int:
        count = self.db.scalar(
            select(func.count(TelemetryPoint.id)).where(
                TelemetryPoint.lap_id == lap_id,
                or_(
                    TelemetryPoint.is_interpolated.is_(False),
                    TelemetryPoint.is_interpolated.is_(None),
                ),
                or_(
                    TelemetryPoint.sample_source.is_(None),
                    TelemetryPoint.sample_source == "",
                    func.lower(TelemetryPoint.sample_source) == "car",
                ),
                TelemetryPoint.distance_m.is_not(None),
                TelemetryPoint.speed_kph.is_not(None),
                TelemetryPoint.throttle_percentage.is_not(None),
                TelemetryPoint.brake_applied.is_not(None),
            )
        )

        return int(count or 0)

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
    def _comparison_warnings(
        *,
        reference_session: RaceSession,
        target_session: RaceSession,
        reference: ComparedLapResponse,
        target: ComparedLapResponse,
    ) -> list[str]:
        warnings: list[str] = []

        if reference.quality_flags:
            warnings.append("REFERENCE_LAP_HAS_QUALITY_FLAGS")

        if target.quality_flags:
            warnings.append("TARGET_LAP_HAS_QUALITY_FLAGS")

        if reference_session.id != target_session.id:
            warnings.append("CROSS_SESSION_COMPARISON")

        if (
            reference.stint_context.compound is not None
            and target.stint_context.compound is not None
            and reference.stint_context.compound
            != target.stint_context.compound
        ):
            warnings.append("DIFFERENT_TYRE_COMPOUNDS")

        return warnings

    @staticmethod
    def _driver_name(driver: Driver) -> str:
        return (
            driver.full_name
            or " ".join(
                part
                for part in [driver.first_name, driver.last_name]
                if part
            )
            or driver.abbreviation
            or driver.driver_number
        )

    @staticmethod
    def _to_float(value: object) -> float | None:
        return float(value) if value is not None else None

    @staticmethod
    def _int_delta(
        target_value: int | None,
        reference_value: int | None,
    ) -> int | None:
        if target_value is None or reference_value is None:
            return None

        return target_value - reference_value

    @staticmethod
    def _float_delta(
        target_value: object,
        reference_value: object,
    ) -> float | None:
        if target_value is None or reference_value is None:
            return None

        return round(
            float(target_value) - float(reference_value),
            3,
        )

    @staticmethod
    def _faster_driver_number(
        *,
        timing_delta: int | None,
        reference_driver_number: str,
        target_driver_number: str,
    ) -> str | None:
        if timing_delta is None or timing_delta == 0:
            return None

        if timing_delta < 0:
            return target_driver_number

        return reference_driver_number