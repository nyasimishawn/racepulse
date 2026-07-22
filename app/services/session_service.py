from collections import defaultdict
from statistics import median
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.driver import Driver
from app.models.lap import Lap
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.team import Team
from app.schemas.strategy import (
    DriverStrategyResponse,
    RaceStrategyResponse,
    StintStrategyResponse,
)


DISCLAIMER = (
    "Stints are derived from stored lap data. Clean-pace metrics only "
    "use accurate green-flag laps without pit entry, pit exit, deletion, "
    "or FastF1-generated timing. They are not proof of tyre degradation, "
    "fuel load, traffic, or team strategy intent."
)


class StrategySessionNotFoundError(LookupError):
    pass


class NonRaceStrategySessionError(ValueError):
    pass


class StrategyQueryService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_race_strategy(
        self,
        *,
        race_session_id: UUID,
        driver_number: str | None,
    ) -> RaceStrategyResponse:
        race_session = self.db.get(RaceSession, race_session_id)

        if race_session is None:
            raise StrategySessionNotFoundError(
                "Race session not found."
            )

        if not self._is_race_session(race_session):
            raise NonRaceStrategySessionError(
                "Strategy analysis currently supports Race sessions only."
            )

        driver_filter = (
            driver_number.strip()
            if driver_number is not None and driver_number.strip()
            else None
        )

        result_statement = (
            select(SessionResult, Driver, Team)
            .join(Driver, SessionResult.driver_id == Driver.id)
            .outerjoin(Team, SessionResult.team_id == Team.id)
            .where(SessionResult.race_session_id == race_session_id)
        )

        if driver_filter is not None:
            result_statement = result_statement.where(
                Driver.driver_number == driver_filter
            )

        result_rows = self.db.execute(result_statement).all()

        driver_context: dict[
            UUID,
            tuple[Driver, SessionResult | None, Team | None],
        ] = {
            driver.id: (driver, result, team)
            for result, driver, team in result_rows
        }

        lap_statement = (
            select(Lap, Driver)
            .join(Driver, Lap.driver_id == Driver.id)
            .where(Lap.race_session_id == race_session_id)
            .order_by(Driver.driver_number, Lap.lap_number)
        )

        if driver_filter is not None:
            lap_statement = lap_statement.where(
                Driver.driver_number == driver_filter
            )

        laps_by_driver: dict[UUID, list[Lap]] = defaultdict(list)
        drivers_from_laps: dict[UUID, Driver] = {}

        for lap, driver in self.db.execute(lap_statement).all():
            laps_by_driver[driver.id].append(lap)
            drivers_from_laps[driver.id] = driver

        all_driver_ids = set(driver_context) | set(laps_by_driver)
        drivers: list[DriverStrategyResponse] = []

        for driver_id in all_driver_ids:
            if driver_id in driver_context:
                driver, result, team = driver_context[driver_id]
            else:
                driver = drivers_from_laps[driver_id]
                result = None
                team = None

            laps = laps_by_driver.get(driver_id, [])

            if not laps:
                drivers.append(
                    DriverStrategyResponse(
                        driver_number=driver.driver_number,
                        abbreviation=driver.abbreviation,
                        driver_name=self._driver_name(driver),
                        team_name=team.name if team else None,
                        team_colour=team.colour if team else None,
                        finishing_position=(
                            result.position if result else None
                        ),
                        classified_position=(
                            result.classified_position
                            if result
                            else None
                        ),
                        stints=[],
                        data_quality_flags=["NO_LAP_DATA"],
                    )
                )
                continue

            segments = self._split_stints(laps)

            stint_responses = [
                self._build_stint_response(
                    sequence=sequence,
                    laps=segment,
                )
                for sequence, segment in enumerate(segments, start=1)
            ]

            driver_flags = sorted(
                {
                    flag
                    for stint in stint_responses
                    for flag in stint.data_quality_flags
                }
            )

            drivers.append(
                DriverStrategyResponse(
                    driver_number=driver.driver_number,
                    abbreviation=driver.abbreviation,
                    driver_name=self._driver_name(driver),
                    team_name=team.name if team else None,
                    team_colour=team.colour if team else None,
                    finishing_position=(
                        result.position if result else None
                    ),
                    classified_position=(
                        result.classified_position if result else None
                    ),
                    stints=stint_responses,
                    data_quality_flags=driver_flags,
                )
            )

        drivers.sort(
            key=lambda item: self._driver_sort_key(
                item.driver_number
            )
        )

        return RaceStrategyResponse(
            race_session_id=race_session_id,
            session_name=race_session.name,
            data_source="LAP_DERIVED_FASTF1",
            driver_count=len(drivers),
            drivers=drivers,
            disclaimer=DISCLAIMER,
        )

    @staticmethod
    def _is_race_session(race_session: RaceSession) -> bool:
        return (
            (race_session.session_identifier or "")
            .strip()
            .upper()
            == "R"
            or (race_session.session_type or "")
            .strip()
            .casefold()
            == "race"
        )

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
    def _driver_sort_key(driver_number: str) -> tuple[int, int | str]:
        if driver_number.isdigit():
            return 0, int(driver_number)

        return 1, driver_number

    def _split_stints(self, laps: list[Lap]) -> list[list[Lap]]:
        if not laps:
            return []

        segments: list[list[Lap]] = []
        current_segment = [laps[0]]

        for lap in laps[1:]:
            previous_lap = current_segment[-1]

            if self._starts_new_stint(
                current_segment=current_segment,
                previous_lap=previous_lap,
                candidate_lap=lap,
            ):
                segments.append(current_segment)
                current_segment = [lap]
            else:
                current_segment.append(lap)

        segments.append(current_segment)

        return segments

    @staticmethod
    def _starts_new_stint(
        *,
        current_segment: list[Lap],
        previous_lap: Lap,
        candidate_lap: Lap,
    ) -> bool:
        source_stint_number = current_segment[0].stint

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

        if source_stint_number is None and (
            previous_lap.pit_in_time_ms is not None
            or candidate_lap.pit_out_time_ms is not None
        ):
            return True

        return False

    def _build_stint_response(
        self,
        *,
        sequence: int,
        laps: list[Lap],
    ) -> StintStrategyResponse:
        first_lap = laps[0]
        last_lap = laps[-1]

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

        clean_laps = [
            lap
            for lap in laps
            if self._is_clean_lap(lap)
        ]

        if not clean_laps:
            flags.append("NO_CLEAN_GREEN_LAPS")
        elif len(clean_laps) < 3:
            flags.append("LIMITED_CLEAN_LAP_SAMPLE")

        clean_times = [
            lap.lap_time_ms
            for lap in clean_laps
            if lap.lap_time_ms is not None
        ]

        return StintStrategyResponse(
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
            lap_count=len(laps),
            tyre_life_start=self._first_tyre_life(laps),
            tyre_life_end=self._last_tyre_life(laps),
            start_position=first_lap.position,
            end_position=last_lap.position,
            pit_in_lap=next(
                (
                    lap.lap_number
                    for lap in reversed(laps)
                    if lap.pit_in_time_ms is not None
                ),
                None,
            ),
            pit_out_lap=next(
                (
                    lap.lap_number
                    for lap in laps
                    if lap.pit_out_time_ms is not None
                ),
                None,
            ),
            clean_lap_count=len(clean_laps),
            clean_median_lap_time_ms=(
                float(median(clean_times))
                if clean_times
                else None
            ),
            clean_fastest_lap_time_ms=(
                min(clean_times)
                if clean_times
                else None
            ),
            data_quality_flags=sorted(set(flags)),
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
    def _first_tyre_life(laps: list[Lap]) -> float | None:
        for lap in laps:
            if lap.tyre_life is not None:
                return float(lap.tyre_life)

        return None

    @staticmethod
    def _last_tyre_life(laps: list[Lap]) -> float | None:
        for lap in reversed(laps):
            if lap.tyre_life is not None:
                return float(lap.tyre_life)

        return None