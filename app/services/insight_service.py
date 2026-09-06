from __future__ import annotations

from collections import defaultdict
from statistics import median
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.driver_consistency import (
    calculate_driver_consistency,
)
from app.analytics.pace_analysis import (
    PacePoint,
    PaceTrend,
    calculate_pace_trend,
)
from app.analytics.sector_analysis import (
    SectorTimes,
    calculate_best_sector_times,
    calculate_sector_deltas,
)
from app.analytics.tyre_degradation import (
    estimate_tyre_pace_proxy,
)
from app.models.driver import Driver
from app.models.lap import Lap
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.team import Team
from app.schemas.insight import (
    DriverConsistencyResponse,
    RacePaceDriverResponse,
    RacePaceInsightResponse,
    RacePaceLapSeriesPointResponse,
    SectorStrengthResponse,
    SessionPaceBenchmarksResponse,
    StintRacePaceResponse,
    TyrePaceProxyResponse,
)


DISCLAIMER = (
    "Race Pace Insights are derived from stored public lap-timing data. "
    "Net pace trends are not direct tyre-wear, fuel-load, ERS, tyre "
    "temperature, traffic, weather, team-strategy, or driver-intent "
    "measurements. A positive trend means lap times became slower over "
    "the selected stint axis; a negative trend means they became faster."
)


class InsightSessionNotFoundError(LookupError):
    pass


class NonRaceInsightSessionError(ValueError):
    pass


class InsightDriverNotFoundError(LookupError):
    pass


class RaceInsightService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_race_pace_insights(
        self,
        *,
        race_session_id: UUID,
        driver_number: str | None,
        include_lap_series: bool,
    ) -> RacePaceInsightResponse:
        race_session = self.db.get(
            RaceSession,
            race_session_id,
        )

        if race_session is None:
            raise InsightSessionNotFoundError(
                "Race session not found."
            )

        if not self._is_race_session(race_session):
            raise NonRaceInsightSessionError(
                "Race Pace Insights currently support Race sessions only."
            )

        driver_filter = (
            driver_number.strip()
            if driver_number is not None
            and driver_number.strip()
            else None
        )

        result_statement = (
            select(SessionResult, Driver, Team)
            .join(Driver, SessionResult.driver_id == Driver.id)
            .outerjoin(Team, SessionResult.team_id == Team.id)
            .where(SessionResult.race_session_id == race_session_id)
        )

        lap_statement = (
            select(Lap, Driver)
            .join(Driver, Lap.driver_id == Driver.id)
            .where(Lap.race_session_id == race_session_id)
            .order_by(Driver.driver_number, Lap.lap_number)
        )

        if driver_filter is not None:
            result_statement = result_statement.where(
                Driver.driver_number == driver_filter
            )
            lap_statement = lap_statement.where(
                Driver.driver_number == driver_filter
            )

        driver_context: dict[
            UUID,
            tuple[Driver, SessionResult | None, Team | None],
        ] = {}

        for result, driver, team in self.db.execute(
            result_statement
        ).all():
            driver_context[driver.id] = (
                driver,
                result,
                team,
            )

        laps_by_driver: dict[UUID, list[Lap]] = defaultdict(list)
        drivers_from_laps: dict[UUID, Driver] = {}

        for lap, driver in self.db.execute(lap_statement).all():
            laps_by_driver[driver.id].append(lap)
            drivers_from_laps[driver.id] = driver

        driver_ids = set(driver_context) | set(laps_by_driver)

        if driver_filter is not None and not driver_ids:
            raise InsightDriverNotFoundError(
                "Driver was not found in this race session."
            )

        clean_laps_by_driver = {
            driver_id: [
                lap
                for lap in laps
                if self._is_clean_green_lap(lap)
            ]
            for driver_id, laps in laps_by_driver.items()
        }

        all_clean_laps = [
            lap
            for laps in clean_laps_by_driver.values()
            for lap in laps
        ]

        session_best_lap_time_ms = (
            min(
                lap.lap_time_ms
                for lap in all_clean_laps
                if lap.lap_time_ms is not None
            )
            if all_clean_laps
            else None
        )

        session_best_sectors = calculate_best_sector_times(
            (
                lap.sector_1_time_ms,
                lap.sector_2_time_ms,
                lap.sector_3_time_ms,
            )
            for lap in all_clean_laps
        )

        benchmark_response = SessionPaceBenchmarksResponse(
            session_best_clean_lap_time_ms=session_best_lap_time_ms,
            session_best_sector_1_time_ms=(
                session_best_sectors.sector_1_time_ms
            ),
            session_best_sector_2_time_ms=(
                session_best_sectors.sector_2_time_ms
            ),
            session_best_sector_3_time_ms=(
                session_best_sectors.sector_3_time_ms
            ),
            drivers_with_clean_laps=sum(
                1
                for laps in clean_laps_by_driver.values()
                if laps
            ),
        )

        drivers: list[RacePaceDriverResponse] = []

        for driver_id in driver_ids:
            if driver_id in driver_context:
                driver, result, team = driver_context[driver_id]
            else:
                driver = drivers_from_laps[driver_id]
                result = None
                team = None

            drivers.append(
                self._build_driver_response(
                    driver=driver,
                    result=result,
                    team=team,
                    laps=laps_by_driver.get(driver_id, []),
                    clean_laps=clean_laps_by_driver.get(
                        driver_id,
                        [],
                    ),
                    session_best_lap_time_ms=session_best_lap_time_ms,
                    session_best_sectors=session_best_sectors,
                    include_lap_series=include_lap_series,
                )
            )

        drivers.sort(
            key=lambda item: (
                item.finishing_position is None,
                item.finishing_position
                if item.finishing_position is not None
                else 999,
                self._driver_sort_key(item.driver_number),
            )
        )

        return RacePaceInsightResponse(
            metric_version="race-pace-v1",
            race_session_id=race_session_id,
            session_name=race_session.name,
            session_type=race_session.session_type,
            data_source="LAP_DERIVED_FASTF1",
            driver_count=len(drivers),
            clean_lap_definition=[
                "lap_time_ms is present",
                "track_status is exactly '1'",
                "is_accurate is true",
                "lap is not deleted",
                "lap has no deleted_reason",
                "lap is not FastF1-generated",
                "lap is not a pit-entry or pit-exit lap",
            ],
            session_benchmarks=benchmark_response,
            drivers=drivers,
            disclaimer=DISCLAIMER,
        )

    def _build_driver_response(
        self,
        *,
        driver: Driver,
        result: SessionResult | None,
        team: Team | None,
        laps: list[Lap],
        clean_laps: list[Lap],
        session_best_lap_time_ms: int | None,
        session_best_sectors: SectorTimes,
        include_lap_series: bool,
    ) -> RacePaceDriverResponse:
        flags: list[str] = []

        if not laps:
            flags.append("NO_LAP_DATA")

        if not clean_laps:
            flags.append("NO_CLEAN_GREEN_LAPS")

        if result is None:
            flags.append("NO_SESSION_RESULT")

        clean_times = [
            lap.lap_time_ms
            for lap in clean_laps
            if lap.lap_time_ms is not None
        ]

        median_clean_lap_time_ms = (
            float(median(clean_times))
            if clean_times
            else None
        )

        fastest_clean_lap_time_ms = (
            min(clean_times)
            if clean_times
            else None
        )

        pace_delta_to_session_best_ms = (
            round(
                median_clean_lap_time_ms
                - session_best_lap_time_ms,
                2,
            )
            if median_clean_lap_time_ms is not None
            and session_best_lap_time_ms is not None
            else None
        )

        consistency = calculate_driver_consistency(clean_times)

        driver_best_sectors = calculate_best_sector_times(
            (
                lap.sector_1_time_ms,
                lap.sector_2_time_ms,
                lap.sector_3_time_ms,
            )
            for lap in clean_laps
        )

        sector_deltas = calculate_sector_deltas(
            driver_best_sectors,
            session_best_sectors,
        )

        stints = [
            self._build_stint_response(
                sequence=sequence,
                laps=segment,
                include_lap_series=include_lap_series,
            )
            for sequence, segment in enumerate(
                self._split_stints(laps),
                start=1,
            )
        ]

        flags.extend(consistency.data_quality_flags)

        for stint in stints:
            flags.extend(stint.data_quality_flags)

        positions_gained = self._positions_gained(result)

        return RacePaceDriverResponse(
            driver_number=driver.driver_number,
            abbreviation=driver.abbreviation,
            driver_name=self._driver_name(driver),
            team_name=team.name if team else None,
            team_colour=team.colour if team else None,
            grid_position=result.grid_position if result else None,
            finishing_position=result.position if result else None,
            positions_gained=positions_gained,
            result_status=result.status if result else None,
            clean_lap_count=len(clean_times),
            median_clean_lap_time_ms=median_clean_lap_time_ms,
            fastest_clean_lap_time_ms=fastest_clean_lap_time_ms,
            pace_delta_to_session_best_ms=(
                pace_delta_to_session_best_ms
            ),
            consistency=DriverConsistencyResponse(
                sample_count=consistency.sample_count,
                median_absolute_deviation_ms=(
                    consistency.median_absolute_deviation_ms
                ),
                p90_p10_spread_ms=(
                    consistency.p90_p10_spread_ms
                ),
                score_10=consistency.score_10,
                data_quality_flags=list(
                    consistency.data_quality_flags
                ),
            ),
            sector_strength=SectorStrengthResponse(
                best_sector_1_time_ms=(
                    driver_best_sectors.sector_1_time_ms
                ),
                best_sector_2_time_ms=(
                    driver_best_sectors.sector_2_time_ms
                ),
                best_sector_3_time_ms=(
                    driver_best_sectors.sector_3_time_ms
                ),
                delta_to_session_best_sector_1_ms=(
                    sector_deltas.sector_1_time_ms
                ),
                delta_to_session_best_sector_2_ms=(
                    sector_deltas.sector_2_time_ms
                ),
                delta_to_session_best_sector_3_ms=(
                    sector_deltas.sector_3_time_ms
                ),
            ),
            stints=stints,
            data_quality_flags=sorted(set(flags)),
        )

    def _build_stint_response(
        self,
        *,
        sequence: int,
        laps: list[Lap],
        include_lap_series: bool,
    ) -> StintRacePaceResponse:
        first_lap = laps[0]
        last_lap = laps[-1]

        clean_laps = [
            lap
            for lap in laps
            if self._is_clean_green_lap(lap)
        ]

        trend_laps, outlier_count = (
            self._exclude_extreme_clean_outliers(clean_laps)
        )

        flags: list[str] = []

        if any(lap.stint is None for lap in laps):
            flags.append("MISSING_SOURCE_STINT")

        compound = next(
            (
                lap.compound
                for lap in laps
                if lap.compound is not None
            ),
            None,
        )

        if compound is None:
            flags.append("UNKNOWN_COMPOUND")

        if not clean_laps:
            flags.append("NO_CLEAN_GREEN_LAPS")
        elif len(clean_laps) < 5:
            flags.append("LIMITED_CLEAN_LAP_SAMPLE")

        if outlier_count:
            flags.append(
                f"EXTREME_CLEAN_LAP_OUTLIERS_EXCLUDED:{outlier_count}"
            )

        points, trend_axis = self._build_trend_points(trend_laps)

        if trend_axis == "STINT_CLEAN_LAP_ORDER":
            flags.append("TREND_AXIS_FALLBACK_TO_STINT_ORDER")

        trend = calculate_pace_trend(
            points,
            axis=trend_axis,
        )

        tyre_proxy = estimate_tyre_pace_proxy(trend)

        flags.extend(trend.data_quality_flags)
        flags.extend(tyre_proxy.data_quality_flags)

        clean_times = [
            lap.lap_time_ms
            for lap in clean_laps
            if lap.lap_time_ms is not None
        ]

        stint_median = (
            float(median(clean_times))
            if clean_times
            else None
        )

        included_trend_lap_ids = {
            lap.id
            for lap in trend_laps
        }

        lap_series = []

        if include_lap_series:
            lap_series = [
                RacePaceLapSeriesPointResponse(
                    lap_number=lap.lap_number,
                    lap_time_ms=lap.lap_time_ms,
                    tyre_life=(
                        float(lap.tyre_life)
                        if lap.tyre_life is not None
                        else None
                    ),
                    delta_to_stint_median_ms=(
                        round(
                            lap.lap_time_ms - stint_median,
                            2,
                        )
                        if stint_median is not None
                        else None
                    ),
                    included_in_trend=(
                        lap.id in included_trend_lap_ids
                    ),
                )
                for lap in clean_laps
                if lap.lap_time_ms is not None
            ]

        return StintRacePaceResponse(
            sequence=sequence,
            source_stint_number=next(
                (
                    lap.stint
                    for lap in laps
                    if lap.stint is not None
                ),
                None,
            ),
            compound=compound,
            fresh_tyre=next(
                (
                    lap.fresh_tyre
                    for lap in laps
                    if lap.fresh_tyre is not None
                ),
                None,
            ),
            start_lap=first_lap.lap_number,
            end_lap=last_lap.lap_number,
            raw_lap_count=len(laps),
            tyre_life_start=self._first_tyre_life(laps),
            tyre_life_end=self._last_tyre_life(laps),
            clean_lap_count=len(clean_times),
            trend_lap_count=len(trend_laps),
            outlier_excluded_lap_count=outlier_count,
            median_clean_lap_time_ms=stint_median,
            fastest_clean_lap_time_ms=(
                min(clean_times)
                if clean_times
                else None
            ),
            opening_pace_ms=trend.opening_pace_ms,
            closing_pace_ms=trend.closing_pace_ms,
            trend_axis=trend.axis,
            net_pace_trend_ms_per_axis_unit=(
                trend.slope_ms_per_axis_unit
            ),
            net_pace_change_ms=trend.net_pace_change_ms,
            trend_direction=trend.direction,
            trend_fit_r_squared=trend.r_squared,
            trend_residual_mad_ms=trend.residual_mad_ms,
            sample_confidence=self._trend_confidence(trend),
            tyre_pace_proxy=TyrePaceProxyResponse(
                classification=tyre_proxy.classification,
                estimated_pace_change_ms_per_axis_unit=(
                    tyre_proxy.estimated_pace_change_ms_per_axis_unit
                ),
                estimated_pace_change_over_stint_ms=(
                    tyre_proxy.estimated_pace_change_over_stint_ms
                ),
                data_quality_flags=list(
                    tyre_proxy.data_quality_flags
                ),
            ),
            data_quality_flags=sorted(set(flags)),
            lap_series=lap_series,
        )

    @staticmethod
    def _is_race_session(
        race_session: RaceSession,
    ) -> bool:
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

    def _split_stints(
        self,
        laps: list[Lap],
    ) -> list[list[Lap]]:
        if not laps:
            return []

        stints: list[list[Lap]] = []
        current_stint = [laps[0]]

        for lap in laps[1:]:
            previous_lap = current_stint[-1]

            if self._starts_new_stint(
                current_stint=current_stint,
                previous_lap=previous_lap,
                candidate_lap=lap,
            ):
                stints.append(current_stint)
                current_stint = [lap]
            else:
                current_stint.append(lap)

        stints.append(current_stint)

        return stints

    @staticmethod
    def _starts_new_stint(
        *,
        current_stint: list[Lap],
        previous_lap: Lap,
        candidate_lap: Lap,
    ) -> bool:
        source_stint_number = current_stint[0].stint

        if (
            candidate_lap.stint is not None
            and candidate_lap.stint != source_stint_number
        ):
            return True

        if (
            previous_lap.compound is not None
            and candidate_lap.compound is not None
            and previous_lap.compound != candidate_lap.compound
        ):
            return True

        return (
            source_stint_number is None
            and (
                previous_lap.pit_in_time_ms is not None
                or candidate_lap.pit_out_time_ms is not None
            )
        )

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
    def _build_trend_points(
        laps: list[Lap],
    ) -> tuple[list[PacePoint], str]:
        tyre_life_values = [
            (
                float(lap.tyre_life)
                if lap.tyre_life is not None
                else None
            )
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
    def _trend_confidence(
        trend: PaceTrend,
    ) -> str:
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
    def _positions_gained(
        result: SessionResult | None,
    ) -> int | None:
        if (
            result is None
            or result.grid_position is None
            or result.position is None
            or result.grid_position <= 0
            or result.position <= 0
        ):
            return None

        return result.grid_position - result.position

    @staticmethod
    def _first_tyre_life(
        laps: list[Lap],
    ) -> float | None:
        for lap in laps:
            if lap.tyre_life is not None:
                return float(lap.tyre_life)

        return None

    @staticmethod
    def _last_tyre_life(
        laps: list[Lap],
    ) -> float | None:
        for lap in reversed(laps):
            if lap.tyre_life is not None:
                return float(lap.tyre_life)

        return None

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
    def _driver_sort_key(
        driver_number: str,
    ) -> tuple[int, int | str]:
        if driver_number.isdigit():
            return 0, int(driver_number)

        return 1, driver_number
