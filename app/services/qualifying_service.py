from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.driver import Driver
from app.models.lap import Lap
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.team import Team
from app.schemas.qualifying import (
    QualifyingReferenceLapResponse,
    QualifyingSummaryResponse,
    QualifyingSummaryRowResponse,
    RaceQualifyingReferenceResponse,
)


class QualifyingSessionNotFoundError(LookupError):
    pass


class NotQualifyingSessionError(ValueError):
    pass


class RaceSessionNotFoundError(LookupError):
    pass


class RaceDriverNotFoundError(LookupError):
    pass


class QualifyingQueryService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_qualifying_summary(
        self,
        qualifying_session_id: UUID,
    ) -> QualifyingSummaryResponse:
        context = self.db.execute(
            select(RaceSession, Meeting)
            .join(Meeting, RaceSession.meeting_id == Meeting.id)
            .where(RaceSession.id == qualifying_session_id)
        ).one_or_none()

        if context is None:
            raise QualifyingSessionNotFoundError(
                "Qualifying session not found."
            )

        qualifying_session, meeting = context

        if not self._is_qualifying(qualifying_session):
            raise NotQualifyingSessionError(
                "This endpoint only accepts a Qualifying session."
            )

        best_laps_by_driver = self._best_laps_by_driver(
            qualifying_session_id
        )

        rows = self.db.execute(
            select(SessionResult, Driver, Team)
            .join(Driver, SessionResult.driver_id == Driver.id)
            .outerjoin(Team, SessionResult.team_id == Team.id)
            .where(
                SessionResult.race_session_id == qualifying_session_id
            )
            .order_by(
                SessionResult.position.is_(None),
                SessionResult.position,
                Driver.driver_number,
            )
        ).all()

        return QualifyingSummaryResponse(
            qualifying_session_id=qualifying_session.id,
            meeting_id=meeting.id,
            meeting_name=meeting.name,
            session_name=qualifying_session.name,
            rows=[
                QualifyingSummaryRowResponse(
                    driver_id=driver.id,
                    driver_number=driver.driver_number,
                    abbreviation=driver.abbreviation,
                    driver_name=self._driver_name(driver),
                    country_code=driver.country_code,
                    team_name=team.name if team else None,
                    team_colour=team.colour if team else None,
                    position=result.position,
                    classified_position=result.classified_position,
                    q1_time_ms=result.q1_time_ms,
                    q2_time_ms=result.q2_time_ms,
                    q3_time_ms=result.q3_time_ms,
                    best_lap=self._to_reference_lap(
                        best_laps_by_driver.get(driver.id)
                    ),
                )
                for result, driver, team in rows
            ],
        )

    def get_race_qualifying_reference(
        self,
        *,
        race_session_id: UUID,
        driver_number: str,
    ) -> RaceQualifyingReferenceResponse:
        race_session = self.db.get(RaceSession, race_session_id)

        if race_session is None:
            raise RaceSessionNotFoundError("Race session not found.")

        driver = self.db.scalar(
            select(Driver)
            .join(
                SessionResult,
                SessionResult.driver_id == Driver.id,
            )
            .where(
                SessionResult.race_session_id == race_session_id,
                Driver.driver_number == driver_number.strip(),
            )
        )

        if driver is None:
            raise RaceDriverNotFoundError(
                "Driver was not found in this race session."
            )

        qualifying_session = self.db.scalar(
            select(RaceSession).where(
                RaceSession.meeting_id == race_session.meeting_id,
                RaceSession.session_identifier == "Q",
            )
        )

        if qualifying_session is None:
            return RaceQualifyingReferenceResponse(
                race_session_id=race_session_id,
                qualifying_session_id=None,
                driver_number=driver.driver_number,
                selection="BEST_STORED_QUALIFYING_LAP",
                q1_time_ms=None,
                q2_time_ms=None,
                q3_time_ms=None,
                reference_lap=None,
                unavailable_reason=(
                    "Qualifying has not been imported for this meeting."
                ),
            )

        qualifying_result = self.db.scalar(
            select(SessionResult).where(
                SessionResult.race_session_id == qualifying_session.id,
                SessionResult.driver_id == driver.id,
            )
        )

        best_lap = self._best_laps_by_driver(
            qualifying_session.id
        ).get(driver.id)

        return RaceQualifyingReferenceResponse(
            race_session_id=race_session_id,
            qualifying_session_id=qualifying_session.id,
            driver_number=driver.driver_number,
            selection="BEST_STORED_QUALIFYING_LAP",
            q1_time_ms=(
                qualifying_result.q1_time_ms
                if qualifying_result
                else None
            ),
            q2_time_ms=(
                qualifying_result.q2_time_ms
                if qualifying_result
                else None
            ),
            q3_time_ms=(
                qualifying_result.q3_time_ms
                if qualifying_result
                else None
            ),
            reference_lap=self._to_reference_lap(best_lap),
            unavailable_reason=(
                None
                if best_lap is not None
                else "No usable qualifying lap has been imported yet."
            ),
        )

    def _best_laps_by_driver(
        self,
        qualifying_session_id: UUID,
    ) -> dict[UUID, tuple[Lap, str]]:
        laps = self.db.scalars(
            select(Lap)
            .where(
                Lap.race_session_id == qualifying_session_id,
                Lap.lap_time_ms.is_not(None),
            )
            .order_by(
                Lap.driver_id,
                Lap.lap_time_ms,
                Lap.lap_number,
            )
        ).all()

        preferred: dict[UUID, tuple[Lap, str]] = {}
        fallback: dict[UUID, tuple[Lap, str]] = {}

        for lap in laps:
            if lap.deleted is True or lap.deleted_reason is not None:
                continue

            if lap.fastf1_generated is True:
                continue

            if lap.pit_in_time_ms is not None:
                continue

            if lap.pit_out_time_ms is not None:
                continue

            is_green_accurate = (
                lap.is_accurate is True
                and lap.track_status == "1"
            )

            if is_green_accurate:
                preferred.setdefault(
                    lap.driver_id,
                    (lap, "GREEN_ACCURATE"),
                )
            else:
                fallback.setdefault(
                    lap.driver_id,
                    (lap, "VALID_FALLBACK"),
                )

        return fallback | preferred

    @staticmethod
    def _is_qualifying(race_session: RaceSession) -> bool:
        return (
            race_session.session_identifier.strip().upper() == "Q"
            or race_session.session_type.strip().casefold()
            == "qualifying"
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
    def _to_reference_lap(
        stored_lap: tuple[Lap, str] | None,
    ) -> QualifyingReferenceLapResponse | None:
        if stored_lap is None:
            return None

        lap, reference_quality = stored_lap

        return QualifyingReferenceLapResponse(
            id=lap.id,
            lap_number=lap.lap_number,
            lap_time_ms=lap.lap_time_ms,
            sector_1_time_ms=lap.sector_1_time_ms,
            sector_2_time_ms=lap.sector_2_time_ms,
            sector_3_time_ms=lap.sector_3_time_ms,
            speed_i1=lap.speed_i1,
            speed_i2=lap.speed_i2,
            speed_fl=lap.speed_fl,
            speed_st=lap.speed_st,
            compound=lap.compound,
            tyre_life=lap.tyre_life,
            track_status=lap.track_status,
            reference_quality=reference_quality,
        )