from __future__ import annotations

from collections import defaultdict
from statistics import median
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.compound_pace import (
    build_driver_compound_baselines,
    normalize_compound,
    score_lap_against_compound_baseline,
)
from app.models.driver import Driver
from app.models.lap import Lap
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.team import Team
from app.schemas.head_to_head import (
    HeadToHeadDriverResponse,
    HeadToHeadPaceDeltaResponse,
    HeadToHeadPaceResponse,
    HeadToHeadPitStopProxyResponse,
    HeadToHeadQualifyingResponse,
    HeadToHeadRaceResultResponse,
    HeadToHeadResponse,
    HeadToHeadScoreCategoryResponse,
    HeadToHeadScoreResponse,
    HeadToHeadSharedCompoundPaceResponse,
    HeadToHeadStintResponse,
)


DISCLAIMER = (
    "Head-to-head pace uses stored public timing. Same-compound "
    "benchmarks avoid direct hard-versus-soft comparisons, but cannot "
    "isolate fuel load, traffic, track evolution, ERS, engine mode, "
    "tyre temperature, or team instructions. Pit-stop figures are "
    "lap-timing proxies, not official stop durations. The score is a "
    "comparison aid, not fantasy scoring."
)

SCORE_DEFINITION = [
    "Race finish: 3 points for the lower finishing position.",
    "Qualifying: 2 points for the lower qualifying position.",
    "Official points: 2 points for the higher stored race points.",
    "Relative execution: 2 points for the lower same-compound delta.",
    "Consistency: 1 point for the lower clean-lap variation.",
]


class HeadToHeadSessionNotFoundError(LookupError):
    pass


class HeadToHeadDriverNotFoundError(LookupError):
    pass


class HeadToHeadNonRaceSessionError(ValueError):
    pass


class HeadToHeadInvalidComparisonError(ValueError):
    pass


class HeadToHeadService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def compare(
        self,
        *,
        race_session_id: UUID,
        driver_a_number: str,
        driver_b_number: str,
    ) -> HeadToHeadResponse:
        race_session, meeting = self._get_context(race_session_id)

        if not self._is_race(race_session):
            raise HeadToHeadNonRaceSessionError(
                "Head-to-head currently supports Race sessions only."
            )

        driver_a_number = self._driver_number(driver_a_number)
        driver_b_number = self._driver_number(driver_b_number)

        if driver_a_number == driver_b_number:
            raise HeadToHeadInvalidComparisonError(
                "Choose two different drivers."
            )

        contexts = self._get_driver_contexts(
            race_session_id,
            [driver_a_number, driver_b_number],
        )
        driver_a, result_a, team_a = contexts[driver_a_number]
        driver_b, result_b, team_b = contexts[driver_b_number]

        laps_by_driver = self._get_laps(
            race_session_id,
            [driver_a.id, driver_b.id],
        )

        qualifying_session = self.db.scalar(
            select(RaceSession).where(
                RaceSession.meeting_id == meeting.id,
                RaceSession.session_identifier == "Q",
            )
        )
        qualifying_results = self._qualifying_results(
            qualifying_session,
            [driver_a.id, driver_b.id],
        )

        response_a, clean_a = self._driver_response(
            driver=driver_a,
            result=result_a,
            team=team_a,
            laps=laps_by_driver.get(driver_a.id, []),
            qualifying_session=qualifying_session,
            qualifying_result=qualifying_results.get(driver_a.id),
        )
        response_b, clean_b = self._driver_response(
            driver=driver_b,
            result=result_b,
            team=team_b,
            laps=laps_by_driver.get(driver_b.id, []),
            qualifying_session=qualifying_session,
            qualifying_result=qualifying_results.get(driver_b.id),
        )

        shared_compounds, comparison_flags = self._shared_compounds(
            driver_a_number,
            clean_a,
            driver_b_number,
            clean_b,
        )

        return HeadToHeadResponse(
            comparison_version="head-to-head-v1",
            race_session_id=race_session.id,
            meeting_id=meeting.id,
            meeting_name=meeting.name,
            race_session_name=race_session.name,
            driver_a=response_a,
            driver_b=response_b,
            shared_compound_pace=shared_compounds,
            pace_delta=HeadToHeadPaceDeltaResponse(
                convention="DRIVER_B_MINUS_DRIVER_A",
                median_clean_lap_time_ms=self._delta(
                    response_a.pace.median_clean_lap_time_ms,
                    response_b.pace.median_clean_lap_time_ms,
                ),
                median_compound_relative_delta_ms=self._delta(
                    response_a.pace.median_compound_relative_delta_ms,
                    response_b.pace.median_compound_relative_delta_ms,
                ),
                sector_1_median_ms=self._delta(
                    response_a.pace.sector_1_median_ms,
                    response_b.pace.sector_1_median_ms,
                ),
                sector_2_median_ms=self._delta(
                    response_a.pace.sector_2_median_ms,
                    response_b.pace.sector_2_median_ms,
                ),
                sector_3_median_ms=self._delta(
                    response_a.pace.sector_3_median_ms,
                    response_b.pace.sector_3_median_ms,
                ),
                consistency_mad_ms=self._delta(
                    response_a.pace.consistency_mad_ms,
                    response_b.pace.consistency_mad_ms,
                ),
            ),
            score=self._score(response_a, response_b),
            score_definition=SCORE_DEFINITION,
            data_quality_flags=comparison_flags,
            disclaimer=DISCLAIMER,
        )

    def _get_context(
        self,
        race_session_id: UUID,
    ) -> tuple[RaceSession, Meeting]:
        context = self.db.execute(
            select(RaceSession, Meeting)
            .join(Meeting, RaceSession.meeting_id == Meeting.id)
            .where(RaceSession.id == race_session_id)
        ).one_or_none()

        if context is None:
            raise HeadToHeadSessionNotFoundError(
                "Race session not found."
            )

        return context

    def _get_driver_contexts(
        self,
        race_session_id: UUID,
        driver_numbers: list[str],
    ) -> dict[str, tuple[Driver, SessionResult, Team | None]]:
        rows = self.db.execute(
            select(SessionResult, Driver, Team)
            .join(Driver, SessionResult.driver_id == Driver.id)
            .outerjoin(Team, SessionResult.team_id == Team.id)
            .where(
                SessionResult.race_session_id == race_session_id,
                Driver.driver_number.in_(driver_numbers),
            )
        ).all()

        contexts = {
            driver.driver_number: (driver, result, team)
            for result, driver, team in rows
        }
        missing = [
            number
            for number in driver_numbers
            if number not in contexts
        ]

        if missing:
            raise HeadToHeadDriverNotFoundError(
                "Driver was not found in this race session: "
                + ", ".join(missing)
            )

        return contexts

    def _get_laps(
        self,
        race_session_id: UUID,
        driver_ids: list[UUID],
    ) -> dict[UUID, list[Lap]]:
        laps_by_driver: dict[UUID, list[Lap]] = defaultdict(list)

        laps = self.db.scalars(
            select(Lap)
            .where(
                Lap.race_session_id == race_session_id,
                Lap.driver_id.in_(driver_ids),
            )
            .order_by(Lap.driver_id, Lap.lap_number)
        ).all()

        for lap in laps:
            laps_by_driver[lap.driver_id].append(lap)

        return laps_by_driver

    def _qualifying_results(
        self,
        qualifying_session: RaceSession | None,
        driver_ids: list[UUID],
    ) -> dict[UUID, SessionResult]:
        if qualifying_session is None:
            return {}

        results = self.db.scalars(
            select(SessionResult).where(
                SessionResult.race_session_id == qualifying_session.id,
                SessionResult.driver_id.in_(driver_ids),
            )
        ).all()

        return {
            result.driver_id: result
            for result in results
        }

    def _driver_response(
        self,
        *,
        driver: Driver,
        result: SessionResult,
        team: Team | None,
        laps: list[Lap],
        qualifying_session: RaceSession | None,
        qualifying_result: SessionResult | None,
    ) -> tuple[HeadToHeadDriverResponse, list[Lap]]:
        clean_laps = [
            lap
            for lap in laps
            if self._is_clean_lap(lap)
        ]
        pace = self._pace(driver.driver_number, clean_laps)
        qualifying = self._qualifying(
            qualifying_session,
            qualifying_result,
        )

        flags = set(pace.data_quality_flags)
        flags.update(qualifying.data_quality_flags)

        if not laps:
            flags.add("NO_LAP_DATA")
        if result.position is None:
            flags.add("RACE_FINISH_POSITION_UNAVAILABLE")

        return (
            HeadToHeadDriverResponse(
                driver_number=driver.driver_number,
                abbreviation=driver.abbreviation,
                driver_name=self._driver_name(driver),
                team_name=team.name if team else None,
                team_colour=team.colour if team else None,
                race_result=HeadToHeadRaceResultResponse(
                    finishing_position=result.position,
                    classified_position=result.classified_position,
                    grid_position=result.grid_position,
                    status=result.status,
                    official_points=(
                        float(result.points)
                        if result.points is not None
                        else None
                    ),
                ),
                qualifying=qualifying,
                pace=pace,
                stints=self._stints(laps),
                pit_stop_proxy=self._pit_proxy(laps),
                data_quality_flags=sorted(flags),
            ),
            clean_laps,
        )

    def _pace(
        self,
        driver_number: str,
        clean_laps: list[Lap],
    ) -> HeadToHeadPaceResponse:
        times = [
            lap.lap_time_ms
            for lap in clean_laps
            if lap.lap_time_ms is not None
        ]
        flags: list[str] = []

        if not times:
            flags.append("NO_CLEAN_GREEN_LAPS")
        elif len(times) < 3:
            flags.append("LIMITED_CLEAN_LAP_SAMPLE")

        baselines = build_driver_compound_baselines(
            {driver_number: clean_laps}
        )
        deltas: list[float] = []

        for lap in clean_laps:
            compound = normalize_compound(lap.compound)
            baseline = (
                baselines.get((driver_number, compound))
                if compound is not None
                else None
            )
            score = score_lap_against_compound_baseline(
                lap,
                baseline,
            )
            if score.delta_to_compound_baseline_ms is not None:
                deltas.append(score.delta_to_compound_baseline_ms)

        if not deltas:
            flags.append("COMPOUND_RELATIVE_PACE_UNAVAILABLE")

        consistency = None
        if len(times) >= 3:
            centre = median(times)
            consistency = round(
                float(median(abs(value - centre) for value in times)),
                2,
            )
        else:
            flags.append("CONSISTENCY_SAMPLE_TOO_SMALL")

        return HeadToHeadPaceResponse(
            clean_lap_count=len(times),
            median_clean_lap_time_ms=self._median(times),
            fastest_clean_lap_time_ms=min(times) if times else None,
            median_compound_relative_delta_ms=(
                round(float(median(deltas)), 2)
                if deltas
                else None
            ),
            compound_relative_sample_count=len(deltas),
            sector_1_median_ms=self._median(
                [
                    lap.sector_1_time_ms
                    for lap in clean_laps
                    if lap.sector_1_time_ms is not None
                ]
            ),
            sector_2_median_ms=self._median(
                [
                    lap.sector_2_time_ms
                    for lap in clean_laps
                    if lap.sector_2_time_ms is not None
                ]
            ),
            sector_3_median_ms=self._median(
                [
                    lap.sector_3_time_ms
                    for lap in clean_laps
                    if lap.sector_3_time_ms is not None
                ]
            ),
            consistency_mad_ms=consistency,
            data_quality_flags=sorted(set(flags)),
        )

    def _qualifying(
        self,
        qualifying_session: RaceSession | None,
        result: SessionResult | None,
    ) -> HeadToHeadQualifyingResponse:
        if qualifying_session is None:
            return HeadToHeadQualifyingResponse(
                qualifying_session_id=None,
                position=None,
                classified_position=None,
                best_available_time_ms=None,
                time_segment=None,
                data_quality_flags=["QUALIFYING_NOT_IMPORTED"],
            )

        if result is None:
            return HeadToHeadQualifyingResponse(
                qualifying_session_id=qualifying_session.id,
                position=None,
                classified_position=None,
                best_available_time_ms=None,
                time_segment=None,
                data_quality_flags=[
                    "DRIVER_NOT_CLASSIFIED_IN_QUALIFYING"
                ],
            )

        for field, segment in [
            ("q3_time_ms", "Q3"),
            ("q2_time_ms", "Q2"),
            ("q1_time_ms", "Q1"),
        ]:
            value = getattr(result, field)
            if value is not None:
                best_time_ms = value
                time_segment = segment
                break
        else:
            best_time_ms = None
            time_segment = None

        flags = []
        if result.position is None:
            flags.append("QUALIFYING_POSITION_UNAVAILABLE")
        if best_time_ms is None:
            flags.append("QUALIFYING_TIME_UNAVAILABLE")

        return HeadToHeadQualifyingResponse(
            qualifying_session_id=qualifying_session.id,
            position=result.position,
            classified_position=result.classified_position,
            best_available_time_ms=best_time_ms,
            time_segment=time_segment,
            data_quality_flags=flags,
        )

    def _stints(
        self,
        laps: list[Lap],
    ) -> list[HeadToHeadStintResponse]:
        if not laps:
            return []

        groups: list[list[Lap]] = [[laps[0]]]
        for lap in laps[1:]:
            current = groups[-1]
            previous = current[-1]
            new_stint = (
                lap.stint is not None
                and lap.stint != current[0].stint
            ) or (
                previous.compound is not None
                and lap.compound is not None
                and previous.compound != lap.compound
            )

            if new_stint:
                groups.append([lap])
            else:
                current.append(lap)

        responses = []
        for sequence, group in enumerate(groups, start=1):
            clean = [
                lap
                for lap in group
                if self._is_clean_lap(lap)
            ]
            times = [
                lap.lap_time_ms
                for lap in clean
                if lap.lap_time_ms is not None
            ]
            flags = []
            if not times:
                flags.append("NO_CLEAN_GREEN_LAPS")
            elif len(times) < 3:
                flags.append("LIMITED_CLEAN_LAP_SAMPLE")

            responses.append(
                HeadToHeadStintResponse(
                    sequence=sequence,
                    source_stint_number=group[0].stint,
                    compound=normalize_compound(
                        group[0].compound
                    ),
                    fresh_tyre=group[0].fresh_tyre,
                    start_lap=group[0].lap_number,
                    end_lap=group[-1].lap_number,
                    lap_count=len(group),
                    tyre_life_start=self._tyre_life(group[0]),
                    tyre_life_end=self._tyre_life(group[-1]),
                    pit_in_lap=next(
                        (
                            lap.lap_number
                            for lap in reversed(group)
                            if lap.pit_in_time_ms is not None
                        ),
                        None,
                    ),
                    pit_out_lap=next(
                        (
                            lap.lap_number
                            for lap in group
                            if lap.pit_out_time_ms is not None
                        ),
                        None,
                    ),
                    clean_lap_count=len(times),
                    clean_median_lap_time_ms=self._median(times),
                    clean_fastest_lap_time_ms=(
                        min(times)
                        if times
                        else None
                    ),
                    data_quality_flags=flags,
                )
            )

        return responses

    @staticmethod
    def _pit_proxy(
        laps: list[Lap],
    ) -> HeadToHeadPitStopProxyResponse:
        entries = [
            lap.lap_number
            for lap in laps
            if lap.pit_in_time_ms is not None
        ]
        exits = [
            lap.lap_number
            for lap in laps
            if lap.pit_out_time_ms is not None
        ]
        flags = ["LAP_TIMING_DERIVED_PIT_STOP_PROXY"]
        if len(entries) != len(exits):
            flags.append("PIT_ENTRY_EXIT_COUNT_MISMATCH")

        return HeadToHeadPitStopProxyResponse(
            observed_pit_entry_lap_numbers=entries,
            observed_pit_exit_lap_numbers=exits,
            inferred_stop_count=len(entries),
            data_quality_flags=flags,
        )

    def _shared_compounds(
        self,
        driver_a_number: str,
        clean_a: list[Lap],
        driver_b_number: str,
        clean_b: list[Lap],
    ) -> tuple[list[HeadToHeadSharedCompoundPaceResponse], list[str]]:
        baselines = build_driver_compound_baselines(
            {
                driver_a_number: clean_a,
                driver_b_number: clean_b,
            }
        )
        compounds_a = {
            compound
            for number, compound in baselines
            if number == driver_a_number
        }
        compounds_b = {
            compound
            for number, compound in baselines
            if number == driver_b_number
        }

        responses = []
        for compound in sorted(compounds_a & compounds_b):
            a = baselines[(driver_a_number, compound)]
            b = baselines[(driver_b_number, compound)]
            delta = round(
                b.robust_fastest_lap_time_ms
                - a.robust_fastest_lap_time_ms,
                2,
            )

            responses.append(
                HeadToHeadSharedCompoundPaceResponse(
                    compound=compound,
                    driver_a_sample_count=a.sample_count,
                    driver_b_sample_count=b.sample_count,
                    driver_a_robust_fastest_lap_time_ms=(
                        a.robust_fastest_lap_time_ms
                    ),
                    driver_b_robust_fastest_lap_time_ms=(
                        b.robust_fastest_lap_time_ms
                    ),
                    driver_b_minus_driver_a_ms=delta,
                    faster_driver_number=(
                        driver_b_number
                        if delta < 0
                        else driver_a_number
                        if delta > 0
                        else None
                    ),
                    data_quality_flags=sorted(
                        {
                            "CROSS_DRIVER_CONTEXT_LIMITED",
                            *a.data_quality_flags,
                            *b.data_quality_flags,
                        }
                    ),
                )
            )

        if not responses:
            return [], ["NO_SHARED_COMPOUND_BASELINE"]

        return responses, []

    def _score(
        self,
        driver_a: HeadToHeadDriverResponse,
        driver_b: HeadToHeadDriverResponse,
    ) -> HeadToHeadScoreResponse:
        categories = [
            self._score_category(
                "RACE_FINISH",
                3,
                driver_a.driver_number,
                driver_b.driver_number,
                driver_a.race_result.finishing_position,
                driver_b.race_result.finishing_position,
                True,
                "LOWER_FINISHING_POSITION_IS_BETTER",
            ),
            self._score_category(
                "QUALIFYING_POSITION",
                2,
                driver_a.driver_number,
                driver_b.driver_number,
                driver_a.qualifying.position,
                driver_b.qualifying.position,
                True,
                "LOWER_QUALIFYING_POSITION_IS_BETTER",
            ),
            self._score_category(
                "OFFICIAL_RACE_POINTS",
                2,
                driver_a.driver_number,
                driver_b.driver_number,
                driver_a.race_result.official_points,
                driver_b.race_result.official_points,
                False,
                "HIGHER_OFFICIAL_RACE_POINTS_IS_BETTER",
            ),
            self._score_category(
                "COMPOUND_RELATIVE_EXECUTION",
                2,
                driver_a.driver_number,
                driver_b.driver_number,
                driver_a.pace.median_compound_relative_delta_ms,
                driver_b.pace.median_compound_relative_delta_ms,
                True,
                "LOWER_DELTA_TO_OWN_COMPOUND_BASELINE_IS_BETTER",
            ),
            self._score_category(
                "CLEAN_LAP_CONSISTENCY",
                1,
                driver_a.driver_number,
                driver_b.driver_number,
                driver_a.pace.consistency_mad_ms,
                driver_b.pace.consistency_mad_ms,
                True,
                "LOWER_CLEAN_LAP_MAD_IS_MORE_CONSISTENT",
            ),
        ]

        points_a = sum(item.driver_a_points for item in categories)
        points_b = sum(item.driver_b_points for item in categories)

        return HeadToHeadScoreResponse(
            driver_a_points=points_a,
            driver_b_points=points_b,
            winner_driver_number=(
                driver_a.driver_number
                if points_a > points_b
                else driver_b.driver_number
                if points_b > points_a
                else None
            ),
            categories=categories,
        )

    @staticmethod
    def _score_category(
        category: str,
        max_points: int,
        driver_a_number: str,
        driver_b_number: str,
        value_a: float | int | None,
        value_b: float | int | None,
        lower_is_better: bool,
        basis: str,
    ) -> HeadToHeadScoreCategoryResponse:
        if value_a is None or value_b is None:
            return HeadToHeadScoreCategoryResponse(
                category=category,
                max_points=max_points,
                winner_driver_number=None,
                driver_a_points=0,
                driver_b_points=0,
                basis=basis,
                data_quality_flags=["METRIC_UNAVAILABLE"],
            )

        if value_a == value_b:
            return HeadToHeadScoreCategoryResponse(
                category=category,
                max_points=max_points,
                winner_driver_number=None,
                driver_a_points=0,
                driver_b_points=0,
                basis=basis,
                data_quality_flags=["METRIC_TIED"],
            )

        driver_a_wins = (
            value_a < value_b
            if lower_is_better
            else value_a > value_b
        )

        return HeadToHeadScoreCategoryResponse(
            category=category,
            max_points=max_points,
            winner_driver_number=(
                driver_a_number
                if driver_a_wins
                else driver_b_number
            ),
            driver_a_points=max_points if driver_a_wins else 0,
            driver_b_points=0 if driver_a_wins else max_points,
            basis=basis,
        )

    @staticmethod
    def _is_race(race_session: RaceSession) -> bool:
        return (
            race_session.session_identifier.strip().upper() == "R"
            or race_session.session_type.strip().casefold() == "race"
        )

    @staticmethod
    def _is_clean_lap(lap: Lap) -> bool:
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

    @staticmethod
    def _driver_number(value: str) -> str:
        value = value.strip()
        if not value:
            raise HeadToHeadInvalidComparisonError(
                "Driver numbers must not be blank."
            )
        return value

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
    def _median(values: list[int]) -> float | None:
        return float(median(values)) if values else None

    @staticmethod
    def _delta(
        value_a: float | None,
        value_b: float | None,
    ) -> float | None:
        if value_a is None or value_b is None:
            return None
        return round(value_b - value_a, 2)

    @staticmethod
    def _tyre_life(lap: Lap) -> float | None:
        return (
            float(lap.tyre_life)
            if lap.tyre_life is not None
            else None
        )