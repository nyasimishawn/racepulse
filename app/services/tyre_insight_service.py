from __future__ import annotations

from statistics import median
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.compound_pace import (
    build_driver_compound_baselines,
    normalize_compound,
    score_lap_against_compound_baseline,
)
from app.analytics.lift_and_coast import (
    TelemetrySample,
    detect_lift_and_coast,
)
from app.analytics.pace_analysis import PacePoint, calculate_pace_trend
from app.analytics.tyre_degradation import estimate_tyre_pace_proxy
from app.models.driver import Driver
from app.models.lap import Lap
from app.models.race_session import RaceSession
from app.models.telemetry_point import TelemetryPoint
from app.schemas.tyre_insight import (
    CompoundBaselineResponse,
    CompoundRelativeLapScoreResponse,
    LiftCoastInsightResponse,
    LiftCoastLapSummaryResponse,
    LiftCoastZoneObservationResponse,
    TheoreticalTyreDegradationResponse,
    TyreInsightResponse,
)


TYRE_INSIGHT_DISCLAIMER = (
    "Tyre insights are theoretical pace proxies from public lap timing. "
    "They are not physical tyre temperature, fuel-load, traffic, ERS, "
    "engine-mode, or private team degradation measurements. Compound "
    "scores compare laps against the same driver's same-compound clean "
    "baseline to avoid hard-vs-soft raw lap comparisons."
)

LIFT_COAST_DISCLAIMER = (
    "Lift-and-coast detection is an inferred observed telemetry pattern: "
    "throttle lift before braking with supporting speed and persistence "
    "evidence where available. It is not proof of driver intent, fuel "
    "target, brake pressure, ERS deployment, or team instruction."
)


class RaceEngineeringSessionNotFoundError(LookupError):
    pass


class RaceEngineeringDriverNotFoundError(LookupError):
    pass


class RaceEngineeringNonRaceSessionError(ValueError):
    pass


class RaceEngineeringInsightService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_tyre_insights(
        self,
        *,
        race_session_id: UUID,
        driver_number: str,
    ) -> TyreInsightResponse:
        race_session = self._get_race_session(race_session_id)
        driver = self._get_driver_for_session(
            race_session_id=race_session_id,
            driver_number=driver_number,
        )
        laps = self._get_driver_laps(
            race_session_id=race_session_id,
            driver_id=driver.id,
        )
        clean_laps = [
            lap
            for lap in laps
            if self._is_clean_green_lap(lap)
        ]

        baselines = build_driver_compound_baselines(
            {driver.driver_number: clean_laps}
        )

        baseline_responses = [
            CompoundBaselineResponse(
                compound=baseline.compound,
                sample_count=baseline.sample_count,
                fastest_lap_time_ms=baseline.fastest_lap_time_ms,
                robust_fastest_lap_time_ms=(
                    baseline.robust_fastest_lap_time_ms
                ),
                baseline_lap_numbers=list(
                    baseline.baseline_lap_numbers
                ),
                data_quality_flags=list(baseline.data_quality_flags),
            )
            for baseline in sorted(
                baselines.values(),
                key=lambda item: item.compound,
            )
        ]

        trend_lap_ids: set[UUID] = set()
        stints: list[TheoreticalTyreDegradationResponse] = []
        for stint_laps in self._split_stints(laps):
            stint_response, included_lap_ids = self._build_stint_degradation(
                stint_laps
            )
            trend_lap_ids.update(included_lap_ids)
            stints.append(stint_response)

        lap_scores = []
        for lap in laps:
            compound = normalize_compound(lap.compound)
            baseline = (
                baselines.get((driver.driver_number, compound))
                if compound is not None
                else None
            )
            score = score_lap_against_compound_baseline(
                lap,
                baseline,
            )
            flags = list(score.data_quality_flags)
            if not self._is_clean_green_lap(lap):
                flags.append("NOT_CLEAN_GREEN_LAP")

            lap_scores.append(
                CompoundRelativeLapScoreResponse(
                    lap_number=score.lap_number,
                    compound=score.compound,
                    tyre_life=(
                        float(lap.tyre_life)
                        if lap.tyre_life is not None
                        else None
                    ),
                    lap_time_ms=score.lap_time_ms,
                    baseline_ms=score.baseline_ms,
                    delta_to_compound_baseline_ms=(
                        score.delta_to_compound_baseline_ms
                    ),
                    band_ms=score.band_ms,
                    classification=score.classification,
                    included_in_degradation_trend=(
                        lap.id in trend_lap_ids
                    ),
                    data_quality_flags=sorted(set(flags)),
                )
            )

        return TyreInsightResponse(
            metric_version="race-engineering-tyre-v1",
            race_session_id=race_session.id,
            driver_number=driver.driver_number,
            driver_name=self._driver_name(driver),
            data_source="LAP_DERIVED_FASTF1",
            baseline_definition=[
                "clean green laps only",
                "same driver and same compound only",
                "robust fastest baseline is the median of up to three fastest clean laps",
                "lap score band is +/- 0.2 seconds around that baseline",
            ],
            compound_baselines=baseline_responses,
            stints=stints,
            lap_scores=lap_scores,
            disclaimer=TYRE_INSIGHT_DISCLAIMER,
        )

    def get_lift_and_coast_insights(
        self,
        *,
        race_session_id: UUID,
        driver_number: str,
        start_lap: int | None = None,
        end_lap: int | None = None,
    ) -> LiftCoastInsightResponse:
        race_session = self._get_race_session(race_session_id)
        driver = self._get_driver_for_session(
            race_session_id=race_session_id,
            driver_number=driver_number,
        )
        laps = self._get_driver_laps(
            race_session_id=race_session_id,
            driver_id=driver.id,
        )
        lap_ids_by_number = {
            lap.lap_number: lap.id
            for lap in laps
            if (start_lap is None or lap.lap_number >= start_lap)
            and (end_lap is None or lap.lap_number <= end_lap)
        }

        samples = self._get_telemetry_samples(
            race_session_id=race_session_id,
            driver_id=driver.id,
            lap_ids_by_number=lap_ids_by_number,
        )
        analysis = detect_lift_and_coast(samples)

        flags = list(analysis.data_quality_flags)
        if not lap_ids_by_number:
            flags.append("NO_LAPS_IN_RANGE")
        elif not samples:
            flags.append("TELEMETRY_NOT_IMPORTED")

        return LiftCoastInsightResponse(
            metric_version="race-engineering-lift-coast-v1",
            race_session_id=race_session.id,
            driver_number=driver.driver_number,
            driver_name=self._driver_name(driver),
            data_source="FULL_TELEMETRY_FASTF1",
            detection_definition=[
                "find braking onsets from brake_applied telemetry",
                "look back 260m before each braking onset",
                "flag throttle at or below 35% before braking",
                "infer lift-and-coast when lift starts at least 70m before braking",
                "mark persistence only when inferred patterns repeat on consecutive laps",
            ],
            lap_summaries=[
                LiftCoastLapSummaryResponse(
                    lap_number=summary.lap_number,
                    inferred_zone_count=summary.inferred_zone_count,
                    strongest_lift_distance_before_brake_m=(
                        summary.strongest_lift_distance_before_brake_m
                    ),
                    classification=summary.classification,
                    evidence_tags=list(summary.evidence_tags),
                )
                for summary in analysis.lap_summaries
            ],
            zone_observations=[
                LiftCoastZoneObservationResponse(
                    lap_number=observation.lap_number,
                    zone_index=observation.zone_index,
                    brake_distance_m=observation.brake_distance_m,
                    lift_start_distance_m=(
                        observation.lift_start_distance_m
                    ),
                    lift_distance_before_brake_m=(
                        observation.lift_distance_before_brake_m
                    ),
                    min_throttle_before_brake=(
                        observation.min_throttle_before_brake
                    ),
                    speed_at_lift_kph=observation.speed_at_lift_kph,
                    speed_at_brake_kph=observation.speed_at_brake_kph,
                    classification=observation.classification,
                    evidence_tags=list(observation.evidence_tags),
                )
                for observation in analysis.zone_observations
            ],
            persistence_lap_ranges=list(analysis.persistence_lap_ranges),
            data_quality_flags=sorted(set(flags)),
            disclaimer=LIFT_COAST_DISCLAIMER,
        )

    def _get_race_session(self, race_session_id: UUID) -> RaceSession:
        race_session = self.db.get(RaceSession, race_session_id)
        if race_session is None:
            raise RaceEngineeringSessionNotFoundError(
                "Race session not found."
            )

        if not self._is_race_session(race_session):
            raise RaceEngineeringNonRaceSessionError(
                "Race engineering insights currently support Race sessions only."
            )

        return race_session

    def _get_driver_for_session(
        self,
        *,
        race_session_id: UUID,
        driver_number: str,
    ) -> Driver:
        statement = (
            select(Driver)
            .join(Lap, Lap.driver_id == Driver.id)
            .where(
                Lap.race_session_id == race_session_id,
                Driver.driver_number == driver_number.strip(),
            )
            .limit(1)
        )
        driver = self.db.execute(statement).scalar_one_or_none()

        if driver is None:
            raise RaceEngineeringDriverNotFoundError(
                "Driver was not found in this race session."
            )

        return driver

    def _get_driver_laps(
        self,
        *,
        race_session_id: UUID,
        driver_id: UUID,
    ) -> list[Lap]:
        return list(
            self.db.execute(
                select(Lap)
                .where(
                    Lap.race_session_id == race_session_id,
                    Lap.driver_id == driver_id,
                )
                .order_by(Lap.lap_number)
            ).scalars()
        )

    def _build_stint_degradation(
        self,
        laps: list[Lap],
    ) -> tuple[TheoreticalTyreDegradationResponse, set[UUID]]:
        clean_laps = [
            lap
            for lap in laps
            if self._is_clean_green_lap(lap)
        ]
        trend_laps, outlier_count = self._exclude_extreme_clean_outliers(
            clean_laps
        )
        trend_points, trend_axis = self._build_trend_points(trend_laps)
        trend = calculate_pace_trend(trend_points, axis=trend_axis)
        tyre_proxy = estimate_tyre_pace_proxy(trend)

        flags = list(trend.data_quality_flags)
        flags.extend(tyre_proxy.data_quality_flags)
        if outlier_count:
            flags.append(
                f"EXTREME_CLEAN_LAP_OUTLIERS_EXCLUDED:{outlier_count}"
            )
        if not clean_laps:
            flags.append("NO_CLEAN_GREEN_LAPS")
        elif len(clean_laps) < 5:
            flags.append("LIMITED_CLEAN_LAP_SAMPLE")
        if any(lap.stint is None for lap in laps):
            flags.append("MISSING_SOURCE_STINT")
        if not any(lap.compound for lap in laps):
            flags.append("UNKNOWN_COMPOUND")
        if trend_axis == "STINT_CLEAN_LAP_ORDER":
            flags.append("TREND_AXIS_FALLBACK_TO_STINT_ORDER")

        return (
            TheoreticalTyreDegradationResponse(
                source_stint_number=next(
                    (
                        lap.stint
                        for lap in laps
                        if lap.stint is not None
                    ),
                    None,
                ),
                compound=next(
                    (
                        normalize_compound(lap.compound)
                        for lap in laps
                        if normalize_compound(lap.compound) is not None
                    ),
                    None,
                ),
                start_lap=laps[0].lap_number,
                end_lap=laps[-1].lap_number,
                clean_lap_count=len(clean_laps),
                trend_axis=trend.axis,
                estimated_pace_change_ms_per_axis_unit=(
                    tyre_proxy.estimated_pace_change_ms_per_axis_unit
                ),
                estimated_pace_change_over_stint_ms=(
                    tyre_proxy.estimated_pace_change_over_stint_ms
                ),
                classification=tyre_proxy.classification,
                sample_confidence=self._trend_confidence(trend),
                data_quality_flags=sorted(set(flags)),
            ),
            {lap.id for lap in trend_laps},
        )

    def _get_telemetry_samples(
        self,
        *,
        race_session_id: UUID,
        driver_id: UUID,
        lap_ids_by_number: dict[int, UUID],
    ) -> list[TelemetrySample]:
        if not lap_ids_by_number:
            return []

        lap_number_by_id = {
            lap_id: lap_number
            for lap_number, lap_id in lap_ids_by_number.items()
        }
        rows = self.db.execute(
            select(TelemetryPoint)
            .where(
                TelemetryPoint.race_session_id == race_session_id,
                TelemetryPoint.driver_id == driver_id,
                TelemetryPoint.lap_id.in_(lap_ids_by_number.values()),
            )
            .order_by(
                TelemetryPoint.lap_id,
                TelemetryPoint.relative_time_ms,
                TelemetryPoint.sample_index,
            )
        ).scalars()

        return [
            TelemetrySample(
                lap_number=lap_number_by_id[row.lap_id],
                distance_m=(
                    float(row.distance_m)
                    if row.distance_m is not None
                    else None
                ),
                relative_time_ms=row.relative_time_ms,
                speed_kph=(
                    float(row.speed_kph)
                    if row.speed_kph is not None
                    else None
                ),
                throttle_percentage=(
                    float(row.throttle_percentage)
                    if row.throttle_percentage is not None
                    else None
                ),
                brake_applied=row.brake_applied,
            )
            for row in rows
        ]

    @staticmethod
    def _is_race_session(race_session: RaceSession) -> bool:
        return (
            (race_session.session_identifier or "").strip().upper()
            == "R"
            or (race_session.session_type or "").strip().casefold()
            == "race"
        )

    @staticmethod
    def _is_clean_green_lap(lap: Lap) -> bool:
        return (
            lap.lap_time_ms is not None
            and lap.track_status == "1"
            and lap.is_accurate is True
            and lap.deleted is not True
            and lap.deleted_reason is None
            and lap.fastf1_generated is not True
            and lap.pit_in_time_ms is None
            and lap.pit_out_time_ms is None
        )

    def _split_stints(self, laps: list[Lap]) -> list[list[Lap]]:
        if not laps:
            return []

        stints: list[list[Lap]] = []
        current_stint = [laps[0]]

        for lap in laps[1:]:
            previous_lap = current_stint[-1]
            source_stint_number = current_stint[0].stint
            starts_new = (
                lap.stint is not None
                and lap.stint != source_stint_number
            ) or (
                previous_lap.compound is not None
                and lap.compound is not None
                and previous_lap.compound != lap.compound
            )

            if starts_new:
                stints.append(current_stint)
                current_stint = [lap]
            else:
                current_stint.append(lap)

        stints.append(current_stint)
        return stints

    @staticmethod
    def _exclude_extreme_clean_outliers(
        laps: list[Lap],
    ) -> tuple[list[Lap], int]:
        if len(laps) < 5:
            return laps, 0

        lap_times = [
            lap.lap_time_ms
            for lap in laps
            if lap.lap_time_ms is not None
        ]
        centre = float(median(lap_times))
        mad = float(
            median(abs(lap_time - centre) for lap_time in lap_times)
        )
        threshold_ms = max(2500.0, 4 * mad)
        retained = [
            lap
            for lap in laps
            if lap.lap_time_ms is not None
            and abs(lap.lap_time_ms - centre) <= threshold_ms
        ]

        if len(retained) < 3:
            return laps, 0

        return retained, len(laps) - len(retained)

    @staticmethod
    def _build_trend_points(laps: list[Lap]) -> tuple[list[PacePoint], str]:
        tyre_life_values = [
            float(lap.tyre_life)
            if lap.tyre_life is not None
            else None
            for lap in laps
        ]
        can_use_tyre_life = (
            bool(laps)
            and all(value is not None for value in tyre_life_values)
            and len(
                {
                    value
                    for value in tyre_life_values
                    if value is not None
                }
            )
            >= 3
        )

        if can_use_tyre_life:
            return (
                [
                    PacePoint(
                        lap_number=lap.lap_number,
                        axis_value=float(tyre_life_values[index]),
                        lap_time_ms=lap.lap_time_ms,
                    )
                    for index, lap in enumerate(laps)
                    if lap.lap_time_ms is not None
                ],
                "TYRE_LIFE",
            )

        return (
            [
                PacePoint(
                    lap_number=lap.lap_number,
                    axis_value=float(index),
                    lap_time_ms=lap.lap_time_ms,
                )
                for index, lap in enumerate(laps, start=1)
                if lap.lap_time_ms is not None
            ],
            "STINT_CLEAN_LAP_ORDER",
        )

    @staticmethod
    def _trend_confidence(trend) -> str:
        if trend.slope_ms_per_axis_unit is None:
            return "UNAVAILABLE"
        if (
            trend.sample_count >= 8
            and trend.r_squared is not None
            and trend.r_squared >= 0.25
        ):
            return "HIGH"
        if trend.sample_count >= 5:
            return "MEDIUM"
        return "LOW"

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

