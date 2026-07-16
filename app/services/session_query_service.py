from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.driver import Driver
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.team import Team
from app.schemas.session import (
    SessionDetailResponse,
    SessionSummaryResponse,
    TimingTowerRowResponse,
)


class SessionQueryService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_sessions(
        self,
        year: int | None = None,
        session_type: str | None = None,
    ) -> list[SessionSummaryResponse]:
        statement = (
            select(RaceSession, Meeting)
            .join(Meeting, RaceSession.meeting_id == Meeting.id)
            .order_by(
                Meeting.year.desc(),
                Meeting.event_date.desc(),
                RaceSession.name,
            )
        )

        if year is not None:
            statement = statement.where(Meeting.year == year)

        if session_type is not None:
            statement = statement.where(
                RaceSession.session_type
                == self._canonical_session_type(session_type)
            )

        rows = self.db.execute(statement).all()

        return [
            self._to_session_summary(race_session, meeting)
            for race_session, meeting in rows
        ]

    def meeting_exists(self, meeting_id: UUID) -> bool:
        return self.db.get(Meeting, meeting_id) is not None

    def list_meeting_sessions(
        self,
        meeting_id: UUID,
    ) -> list[SessionSummaryResponse]:
        rows = self.db.execute(
            select(RaceSession, Meeting)
            .join(Meeting, RaceSession.meeting_id == Meeting.id)
            .where(Meeting.id == meeting_id)
            .order_by(RaceSession.started_at, RaceSession.name)
        ).all()

        return [
            self._to_session_summary(race_session, meeting)
            for race_session, meeting in rows
        ]

    def get_session_detail(
        self,
        session_id: UUID,
    ) -> SessionDetailResponse | None:
        row = self.db.execute(
            select(RaceSession, Meeting)
            .join(Meeting, RaceSession.meeting_id == Meeting.id)
            .where(RaceSession.id == session_id)
        ).one_or_none()

        if row is None:
            return None

        race_session, meeting = row
        summary = self._to_session_summary(race_session, meeting)

        return SessionDetailResponse(
            **summary.model_dump(),
            results=self.get_timing_tower(session_id),
        )

    def get_timing_tower(
        self,
        session_id: UUID,
    ) -> list[TimingTowerRowResponse]:
        rows = self.db.execute(
            select(SessionResult, Driver, Team)
            .join(Driver, SessionResult.driver_id == Driver.id)
            .outerjoin(Team, SessionResult.team_id == Team.id)
            .where(SessionResult.race_session_id == session_id)
            .order_by(
                SessionResult.position.is_(None),
                SessionResult.position,
                Driver.driver_number,
            )
        ).all()

        timing_rows: list[TimingTowerRowResponse] = []

        for result, driver, team in rows:
            driver_name = (
                driver.full_name
                or " ".join(
                    part
                    for part in [driver.first_name, driver.last_name]
                    if part
                )
                or driver.abbreviation
                or driver.driver_number
            )

            timing_rows.append(
                TimingTowerRowResponse(
                    position=result.position,
                    classified_position=result.classified_position,
                    grid_position=result.grid_position,
                    q1_time_ms=result.q1_time_ms,
                    q2_time_ms=result.q2_time_ms,
                    q3_time_ms=result.q3_time_ms,
                    driver_number=driver.driver_number,
                    abbreviation=driver.abbreviation,
                    driver_name=driver_name,
                    country_code=driver.country_code,
                    team_name=team.name if team else None,
                    team_colour=team.colour if team else None,
                    status=result.status,
                    points=result.points,
                )
            )

        return timing_rows

    @staticmethod
    def _canonical_session_type(session_type: str) -> str:
        aliases = {
            "R": "Race",
            "RACE": "Race",
            "Q": "Qualifying",
            "QUALIFYING": "Qualifying",
            "FP1": "Practice 1",
            "FP2": "Practice 2",
            "FP3": "Practice 3",
            "S": "Sprint",
            "SPRINT": "Sprint",
        }

        normalized = session_type.strip().upper()

        return aliases.get(normalized, session_type.strip())

    @staticmethod
    def _to_session_summary(
        race_session: RaceSession,
        meeting: Meeting,
    ) -> SessionSummaryResponse:
        return SessionSummaryResponse(
            id=race_session.id,
            meeting_id=meeting.id,
            source=meeting.source,
            year=meeting.year,
            meeting_name=meeting.name,
            official_meeting_name=meeting.official_name,
            country_name=meeting.country_name,
            location=meeting.location,
            event_date=meeting.event_date,
            session_name=race_session.name,
            session_identifier=race_session.session_identifier,
            session_type=race_session.session_type,
            started_at=race_session.started_at,
            created_at=race_session.created_at,
        )