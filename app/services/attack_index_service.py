from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.attack_index import (
    AttackIndexResult,
    InsufficientTelemetryError,
    TelemetrySample,
    calculate_attack_index,
)
from app.models.driver import Driver
from app.models.lap import Lap
from app.models.race_session import RaceSession
from app.models.telemetry_point import TelemetryPoint
from app.schemas.attack_index import (
    AttackIndexComponentsResponse,
    AttackIndexResponse,
)
from app.services.qualifying_service import QualifyingQueryService


DISCLAIMER = (
    "Attack Index is a telemetry-derived driving-effort proxy. "
    "It cannot prove driver intent or reveal fuel load, ERS mode, "
    "tyre temperature, traffic, or team instructions."
)


class RaceLapNotFoundError(LookupError):
    pass


class NonRaceSessionError(ValueError):
    pass


class AttackIndexService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def assess(
        self,
        *,
        race_session_id: UUID,
        driver_number: str,
        race_lap_number: int,
    ) -> AttackIndexResponse:
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
                Lap.lap_number == race_lap_number,
            )
        ).one_or_none()

        if context is None:
            raise RaceLapNotFoundError(
                "The requested race lap has not been imported."
            )

        race_lap, driver, race_session = context

        is_race_session = (
            (race_session.session_identifier or "").strip().upper() == "R"
            or (race_session.session_type or "").strip().casefold() == "race"
        )

        if not is_race_session:
            raise NonRaceSessionError(
                "Attack Index currently supports Race laps only. "
                "Select the Race session (R), not Qualifying (Q)."
            )

        reference = QualifyingQueryService(
            self.db
        ).get_race_qualifying_reference(
            race_session_id=race_session_id,
            driver_number=driver.driver_number,
        )

        if (
            reference.qualifying_session_id is None
            or reference.reference_lap is None
        ):
            return self._ineligible_response(
                race_session_id=race_session.id,
                driver_number=driver.driver_number,
                race_lap=race_lap,
                qualifying_session_id=reference.qualifying_session_id,
                qualifying_lap=None,
                qualifying_reference_quality=None,
                reasons=[
                    reference.unavailable_reason
                    or "No qualifying reference lap is available."
                ],
            )

        qualifying_lap = self.db.get(
            Lap,
            reference.reference_lap.id,
        )

        if qualifying_lap is None:
            return self._ineligible_response(
                race_session_id=race_session.id,
                driver_number=driver.driver_number,
                race_lap=race_lap,
                qualifying_session_id=reference.qualifying_session_id,
                qualifying_lap=None,
                qualifying_reference_quality=None,
                reasons=[
                    "The qualifying reference lap could not be loaded."
                ],
            )

        reasons = [
            *self._lap_eligibility_reasons(
                lap=race_lap,
                label="Race lap",
            ),
            *self._lap_eligibility_reasons(
                lap=qualifying_lap,
                label="Qualifying reference lap",
            ),
        ]

        if reasons:
            return self._ineligible_response(
                race_session_id=race_session.id,
                driver_number=driver.driver_number,
                race_lap=race_lap,
                qualifying_session_id=reference.qualifying_session_id,
                qualifying_lap=qualifying_lap,
                qualifying_reference_quality=(
                    reference.reference_lap.reference_quality
                ),
                reasons=reasons,
            )

        race_samples = self._raw_telemetry_samples(race_lap.id)
        qualifying_samples = self._raw_telemetry_samples(
            qualifying_lap.id
        )

        if not race_samples:
            reasons.append(
                "Race-lap telemetry is missing. Import it before scoring."
            )

        if not qualifying_samples:
            reasons.append(
                "Qualifying-lap telemetry is missing. Import it before scoring."
            )

        if reasons:
            return self._ineligible_response(
                race_session_id=race_session.id,
                driver_number=driver.driver_number,
                race_lap=race_lap,
                qualifying_session_id=reference.qualifying_session_id,
                qualifying_lap=qualifying_lap,
                qualifying_reference_quality=(
                    reference.reference_lap.reference_quality
                ),
                reasons=reasons,
            )

        try:
            result = calculate_attack_index(
                qualifying_samples=qualifying_samples,
                race_samples=race_samples,
            )
        except InsufficientTelemetryError as error:
            return self._ineligible_response(
                race_session_id=race_session.id,
                driver_number=driver.driver_number,
                race_lap=race_lap,
                qualifying_session_id=reference.qualifying_session_id,
                qualifying_lap=qualifying_lap,
                qualifying_reference_quality=(
                    reference.reference_lap.reference_quality
                ),
                reasons=[str(error)],
            )

        return self._eligible_response(
            race_session_id=race_session.id,
            qualifying_session_id=reference.qualifying_session_id,
            driver_number=driver.driver_number,
            race_lap=race_lap,
            qualifying_lap=qualifying_lap,
            qualifying_reference_quality=(
                reference.reference_lap.reference_quality
            ),
            result=result,
        )

    def _raw_telemetry_samples(
        self,
        lap_id: UUID,
    ) -> list[TelemetrySample]:
        points = self.db.scalars(
            select(TelemetryPoint)
            .where(TelemetryPoint.lap_id == lap_id)
            .order_by(TelemetryPoint.relative_time_ms)
        ).all()

        samples: list[TelemetrySample] = []

        for point in points:
            source = (point.sample_source or "").casefold()

            if point.is_interpolated:
                continue

            if source not in {"", "car"}:
                continue

            if (
                point.distance_m is None
                or point.speed_kph is None
                or point.throttle_percentage is None
                or point.brake_applied is None
            ):
                continue

            samples.append(
                TelemetrySample(
                    distance_m=float(point.distance_m),
                    speed_kph=float(point.speed_kph),
                    throttle_percentage=float(
                        point.throttle_percentage
                    ),
                    brake_applied=point.brake_applied,
                )
            )

        return samples

    @staticmethod
    def _lap_eligibility_reasons(
        *,
        lap: Lap,
        label: str,
    ) -> list[str]:
        reasons: list[str] = []

        if lap.lap_time_ms is None:
            reasons.append(f"{label} has no complete lap time.")

        if lap.deleted is True or lap.deleted_reason is not None:
            reasons.append(f"{label} was deleted or invalidated.")

        if lap.is_accurate is not True:
            reasons.append(f"{label} is not marked as accurate.")

        if lap.fastf1_generated is True:
            reasons.append(f"{label} was generated by FastF1.")

        if lap.pit_in_time_ms is not None:
            reasons.append(f"{label} includes a pit entry.")

        if lap.pit_out_time_ms is not None:
            reasons.append(f"{label} includes a pit exit.")

        if lap.track_status != "1":
            reasons.append(
                f"{label} was not completed under a clear track status."
            )

        return reasons

    @staticmethod
    def _eligible_response(
        *,
        race_session_id: UUID,
        qualifying_session_id: UUID,
        driver_number: str,
        race_lap: Lap,
        qualifying_lap: Lap,
        qualifying_reference_quality: str,
        result: AttackIndexResult,
    ) -> AttackIndexResponse:
        lap_delta = (
            race_lap.lap_time_ms - qualifying_lap.lap_time_ms
            if (
                race_lap.lap_time_ms is not None
                and qualifying_lap.lap_time_ms is not None
            )
            else None
        )

        return AttackIndexResponse(
            metric_version=result.metric_version,
            eligible=True,
            attack_score_10=result.attack_score_10,
            classification=result.classification,
            confidence=result.confidence,
            race_session_id=race_session_id,
            qualifying_session_id=qualifying_session_id,
            driver_number=driver_number,
            race_lap_id=race_lap.id,
            race_lap_number=race_lap.lap_number,
            qualifying_lap_id=qualifying_lap.id,
            qualifying_lap_number=qualifying_lap.lap_number,
            race_lap_time_ms=race_lap.lap_time_ms,
            qualifying_lap_time_ms=qualifying_lap.lap_time_ms,
            lap_time_delta_to_qualifying_ms=lap_delta,
            qualifying_reference_quality=qualifying_reference_quality,
            components=AttackIndexComponentsResponse(
                qualifying_full_throttle_share=(
                    result.qualifying_full_throttle_share
                ),
                race_full_throttle_share=result.race_full_throttle_share,
                full_throttle_share_delta=(
                    result.full_throttle_share_delta
                ),
                qualifying_brake_zone_share=(
                    result.qualifying_brake_zone_share
                ),
                race_brake_zone_share=result.race_brake_zone_share,
                brake_zone_share_delta=result.brake_zone_share_delta,
                qualifying_coast_candidate_distance_m=(
                    result.qualifying_coast_candidate_distance_m
                ),
                race_coast_candidate_distance_m=(
                    result.race_coast_candidate_distance_m
                ),
                extra_coast_candidate_distance_m=(
                    result.extra_coast_candidate_distance_m
                ),
                qualifying_sample_count=result.qualifying_sample_count,
                race_sample_count=result.race_sample_count,
                common_bin_count=result.common_bin_count,
            ),
            ineligibility_reasons=[],
            disclaimer=result.disclaimer,
        )

    @staticmethod
    def _ineligible_response(
        *,
        race_session_id: UUID,
        driver_number: str,
        race_lap: Lap,
        qualifying_session_id: UUID | None,
        qualifying_lap: Lap | None,
        qualifying_reference_quality: str | None,
        reasons: list[str],
    ) -> AttackIndexResponse:
        lap_delta = (
            race_lap.lap_time_ms - qualifying_lap.lap_time_ms
            if (
                qualifying_lap is not None
                and race_lap.lap_time_ms is not None
                and qualifying_lap.lap_time_ms is not None
            )
            else None
        )

        return AttackIndexResponse(
            metric_version="attack-index-v1",
            eligible=False,
            attack_score_10=None,
            classification=None,
            confidence=None,
            race_session_id=race_session_id,
            qualifying_session_id=qualifying_session_id,
            driver_number=driver_number,
            race_lap_id=race_lap.id,
            race_lap_number=race_lap.lap_number,
            qualifying_lap_id=(
                qualifying_lap.id if qualifying_lap else None
            ),
            qualifying_lap_number=(
                qualifying_lap.lap_number if qualifying_lap else None
            ),
            race_lap_time_ms=race_lap.lap_time_ms,
            qualifying_lap_time_ms=(
                qualifying_lap.lap_time_ms
                if qualifying_lap
                else None
            ),
            lap_time_delta_to_qualifying_ms=lap_delta,
            qualifying_reference_quality=qualifying_reference_quality,
            components=None,
            ineligibility_reasons=reasons,
            disclaimer=DISCLAIMER,
        )