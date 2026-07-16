from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.driver import Driver
from app.models.import_job import DataSource, ImportJob, ImportJobStatus
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.team import Team
from app.providers.fastf1_provider import FastF1Provider


class ImportJobNotFoundError(LookupError):
    pass


class ImportJobStateError(RuntimeError):
    pass


class SessionImportError(RuntimeError):
    pass


class SessionImportService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.fastf1_provider = FastF1Provider()

    def run(self, job_id: UUID) -> ImportJob:
        job = self.db.get(ImportJob, job_id)

        if job is None:
            raise ImportJobNotFoundError("Import job not found.")

        if job.source != DataSource.FASTF1:
            raise ImportJobStateError(
                "Only FASTF1 imports are supported at this stage."
            )

        if job.status == ImportJobStatus.RUNNING:
            raise ImportJobStateError("This import job is already running.")

        if (
            job.status == ImportJobStatus.COMPLETED
            and job.imported_session_id is not None
        ):
            return job

        job.status = ImportJobStatus.RUNNING
        job.progress_percentage = 5
        job.error_message = None
        job.started_at = datetime.now(UTC)
        job.completed_at = None
        self.db.commit()

        try:
            fastf1_session = self.fastf1_provider.load_results_session(
                year=job.year,
                event_name=job.event_name,
                session_identifier=self._fastf1_identifier(job.session_type),
            )

            job.progress_percentage = 45

            meeting = self._upsert_meeting(job, fastf1_session)
            self.db.flush()

            race_session = self._upsert_race_session(
                job,
                meeting,
                fastf1_session,
            )
            self.db.flush()

            for _, result_row in fastf1_session.results.iterrows():
                driver = self._upsert_driver(result_row)
                team = self._upsert_team(result_row)

                self.db.flush()

                self._upsert_session_result(
                    race_session=race_session,
                    driver=driver,
                    team=team,
                    result_row=result_row,
                )

            job.progress_percentage = 90
            job.imported_session_id = race_session.id
            job.status = ImportJobStatus.COMPLETED
            job.progress_percentage = 100
            job.completed_at = datetime.now(UTC)

            self.db.commit()
            self.db.refresh(job)

            return job

        except Exception as error:
            self.db.rollback()

            failed_job = self.db.get(ImportJob, job_id)

            if failed_job is not None:
                failed_job.status = ImportJobStatus.FAILED
                failed_job.error_message = (
                    f"{type(error).__name__}: {error}"
                )[:2000]
                failed_job.completed_at = datetime.now(UTC)

                self.db.commit()

            raise SessionImportError(
                "FastF1 import failed. Check the import job for details."
            ) from error

    def _upsert_meeting(self, job: ImportJob, fastf1_session) -> Meeting:
        event = fastf1_session.event
        source = job.source.value

        meeting_name = self._text(event.get("EventName")) or job.event_name

        meeting = self.db.scalar(
            select(Meeting).where(
                Meeting.source == source,
                Meeting.year == job.year,
                Meeting.name == meeting_name,
            )
        )

        if meeting is None:
            meeting = Meeting(
                source=source,
                year=job.year,
                name=meeting_name,
            )
            self.db.add(meeting)

        meeting.official_name = self._text(event.get("OfficialEventName"))
        meeting.country_name = self._text(event.get("Country"))
        meeting.location = self._text(event.get("Location"))
        meeting.event_date = self._datetime(event.get("EventDate"))

        return meeting

    def _upsert_race_session(
        self,
        job: ImportJob,
        meeting: Meeting,
        fastf1_session,
    ) -> RaceSession:
        session_identifier = self._fastf1_identifier(job.session_type)
        session_type = self._canonical_session_type(
            self._text(fastf1_session.name) or job.session_type
        )

        race_session = self.db.scalar(
            select(RaceSession).where(
                RaceSession.meeting_id == meeting.id,
                RaceSession.session_identifier == session_identifier,
            )
        )

        if race_session is None:
            race_session = RaceSession(
                meeting_id=meeting.id,
                session_identifier=session_identifier,
                name=fastf1_session.name or session_type,
                session_type=session_type,
            )
            self.db.add(race_session)

        race_session.name = fastf1_session.name or session_type
        race_session.session_type = session_type
        race_session.started_at = self._datetime(fastf1_session.date)

        return race_session

    def _upsert_team(self, result_row: pd.Series) -> Team | None:
        source = DataSource.FASTF1.value
        team_name = self._text(result_row.get("TeamName"))

        if team_name is None:
            return None

        source_identifier = (
            self._text(result_row.get("TeamId"))
            or f"name:{team_name.casefold()}"
        )

        team = self.db.scalar(
            select(Team).where(
                Team.source == source,
                Team.source_identifier == source_identifier,
            )
        )

        if team is None:
            team = Team(
                source=source,
                source_identifier=source_identifier,
                name=team_name,
            )
            self.db.add(team)

        team.name = team_name
        team.colour = self._text(result_row.get("TeamColor"))

        return team

    def _upsert_driver(self, result_row: pd.Series) -> Driver:
        source = DataSource.FASTF1.value
        driver_number = self._text(result_row.get("DriverNumber"))

        if driver_number is None:
            raise ValueError("FastF1 returned a result without a driver number.")

        source_identifier = (
            self._text(result_row.get("DriverId"))
            or f"number:{driver_number}"
        )

        driver = self.db.scalar(
            select(Driver).where(
                Driver.source == source,
                Driver.source_identifier == source_identifier,
            )
        )

        if driver is None:
            driver = Driver(
                source=source,
                source_identifier=source_identifier,
                driver_number=driver_number,
            )
            self.db.add(driver)

        driver.driver_number = driver_number
        driver.abbreviation = self._text(result_row.get("Abbreviation"))
        driver.first_name = self._text(result_row.get("FirstName"))
        driver.last_name = self._text(result_row.get("LastName"))
        driver.full_name = self._text(result_row.get("FullName"))
        driver.country_code = self._text(result_row.get("CountryCode"))

        return driver

    def _upsert_session_result(
        self,
        *,
        race_session: RaceSession,
        driver: Driver,
        team: Team | None,
        result_row: pd.Series,
    ) -> SessionResult:
        result = self.db.scalar(
            select(SessionResult).where(
                SessionResult.race_session_id == race_session.id,
                SessionResult.driver_id == driver.id,
            )
        )

        if result is None:
            result = SessionResult(
                race_session_id=race_session.id,
                driver_id=driver.id,
            )
            self.db.add(result)

        result.team_id = team.id if team is not None else None
        result.position = self._integer(result_row.get("Position"))
        result.classified_position = self._text(
            result_row.get("ClassifiedPosition")
        )
        result.grid_position = self._integer(result_row.get("GridPosition"))

        result.q1_time_ms = self._timedelta_ms(result_row.get("Q1"))
        result.q2_time_ms = self._timedelta_ms(result_row.get("Q2"))
        result.q3_time_ms = self._timedelta_ms(result_row.get("Q3"))

        result.status = self._text(result_row.get("Status"))
        result.points = self._decimal(result_row.get("Points"))

        return result

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
    def _fastf1_identifier(session_type: str) -> str:
        aliases = {
            "R": "R",
            "RACE": "R",
            "Q": "Q",
            "QUALIFYING": "Q",
            "FP1": "FP1",
            "FP2": "FP2",
            "FP3": "FP3",
            "S": "S",
            "SPRINT": "S",
        }

        normalized = session_type.strip().upper()

        return aliases.get(normalized, session_type.strip())

    @staticmethod
    def _text(value: object) -> str | None:
        if value is None:
            return None

        try:
            if bool(pd.isna(value)):
                return None
        except (TypeError, ValueError):
            pass

        text = str(value).strip()

        if not text or text.lower() in {"nan", "nat", "none"}:
            return None

        return text

    @staticmethod
    def _integer(value: object) -> int | None:
        text = SessionImportService._text(value)

        if text is None:
            return None

        try:
            return int(float(text))
        except ValueError:
            return None

    @staticmethod
    def _decimal(value: object) -> Decimal | None:
        text = SessionImportService._text(value)

        if text is None:
            return None

        try:
            return Decimal(text)
        except InvalidOperation:
            return None

    @staticmethod
    def _timedelta_ms(value: object) -> int | None:
        if value is None:
            return None

        try:
            delta = pd.Timedelta(value)

            if pd.isna(delta):
                return None

            return int(round(delta.total_seconds() * 1000))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _datetime(value: object) -> datetime | None:
        if value is None:
            return None

        try:
            if bool(pd.isna(value)):
                return None
        except (TypeError, ValueError):
            pass

        if isinstance(value, pd.Timestamp):
            value = value.to_pydatetime()

        if not isinstance(value, datetime):
            return None

        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)

        return value