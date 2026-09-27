"""Development-only, owner-scoped replay of the 2026 Barcelona weekend."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings
from app.models.driver import Driver
from app.models.fantasy import FantasyQuestionResolution
from app.models.lap import Lap
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.schemas.fantasy import FantasyQuestionResolutionRequest
from app.services.fantasy_service import FantasyService


SOURCE = "FANTASY_REPLAY"
START = datetime(2026, 6, 11, 9, tzinfo=UTC)
FP1_START = datetime(2026, 6, 12, 9, 30, tzinfo=UTC)
Q_START = datetime(2026, 6, 13, 12, tzinfo=UTC)
R_START = datetime(2026, 6, 14, 13, tzinfo=UTC)
EVENTS = (
    ("FP1", "Practice 1", FP1_START, FP1_START + timedelta(hours=1)),
    ("Q", "Qualifying", Q_START, Q_START + timedelta(hours=1)),
    ("R", "Race", R_START, R_START + timedelta(hours=2)),
)
END = EVENTS[-1][3]
SPEEDS = {60, 600, 3600, 14400}
OFFICIAL_SCHEDULE = "https://www.formula1.com/en/racing/2026/barcelona-catalunya"
OFFICIAL_FP1 = (
    "https://www.formula1.com/en/results/2026/races/1287/"
    "barcelona/practice/1"
)
OFFICIAL_Q = (
    "https://www.formula1.com/en/results/2026/races/1287/"
    "barcelona/qualifying"
)
OFFICIAL_FASTEST = (
    "https://www.formula1.com/en/results/2026/races/1287/"
    "barcelona/fastest-laps"
)


class ReplayError(ValueError):
    pass


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class FantasyReplayService:
    def __init__(self, db: Session, now: datetime | None = None) -> None:
        self.db = db
        self.now = utc(now or datetime.now(UTC))

    @staticmethod
    def available() -> bool:
        return settings.environment == "development"

    def _require_development(self) -> None:
        if not self.available():
            raise ReplayError("Fantasy replay is available in development only.")

    def current(self, owner_id: UUID) -> dict | None:
        self._require_development()
        races = self.db.scalars(
            select(RaceSession)
            .join(Meeting, Meeting.id == RaceSession.meeting_id)
            .where(Meeting.source == SOURCE, RaceSession.session_identifier == "R")
            .order_by(RaceSession.created_at.desc(), RaceSession.id.desc())
        ).all()
        for race in races:
            metadata = self._metadata(race)
            if (
                metadata.get("owner_profile_id") == str(owner_id)
                and metadata.get("is_current", True)
            ):
                return self.state(race.id, owner_id)
        return None

    def create(self, owner_id: UUID) -> dict:
        self._require_development()
        source = self.db.scalar(
            select(RaceSession)
            .join(Meeting, Meeting.id == RaceSession.meeting_id)
            .where(
                Meeting.year == 2026,
                Meeting.name == "Barcelona Grand Prix",
                RaceSession.session_identifier == "R",
                Meeting.source != SOURCE,
            )
            .order_by(RaceSession.created_at.desc())
        )
        if source is None:
            raise ReplayError(
                "Import the 2026 Barcelona Grand Prix race results first."
            )
        results = self.db.scalars(
            select(SessionResult).where(SessionResult.race_session_id == source.id)
        ).all()
        if len(results) < 3 or not {1, 2, 3}.issubset(
            {result.position for result in results}
        ):
            raise ReplayError("Barcelona race classification is incomplete.")

        previous = self.db.scalars(
            select(RaceSession)
            .join(Meeting, Meeting.id == RaceSession.meeting_id)
            .where(Meeting.source == SOURCE, RaceSession.session_identifier == "R")
        ).all()
        for old_race in previous:
            old_metadata = self._metadata(old_race)
            if old_metadata.get("owner_profile_id") == str(owner_id):
                old_race.source_metadata = {
                    "fantasy_replay": {**old_metadata, "is_current": False}
                }
                flag_modified(old_race, "source_metadata")

        meeting = Meeting(
            source=SOURCE,
            year=2026,
            name=f"Barcelona Grand Prix Replay {uuid4().hex[:12]}",
            event_date=R_START,
        )
        self.db.add(meeting)
        self.db.flush()
        sessions = {}
        for identifier, name, starts_at, _ in EVENTS:
            session = RaceSession(
                meeting_id=meeting.id,
                name=name,
                session_identifier=identifier,
                session_type=(
                    "Practice" if identifier == "FP1"
                    else "Qualifying" if identifier == "Q"
                    else "Race"
                ),
                started_at=starts_at,
                source_metadata=(
                    {
                        "fantasy_replay": {
                            "owner_profile_id": str(owner_id),
                            "is_current": True,
                            "source_race_id": str(source.id),
                            "base_virtual_at": START.isoformat(),
                            "anchor_real_at": self.now.isoformat(),
                            "speed": 3600,
                            "playing": False,
                        }
                    }
                    if identifier == "R" else {}
                ),
            )
            self.db.add(session)
            self.db.flush()
            sessions[identifier] = session
        self.db.add_all(
            SessionResult(
                race_session_id=sessions["R"].id,
                driver_id=result.driver_id,
                team_id=result.team_id,
            )
            for result in results
        )
        self.db.commit()
        return self.state(sessions["R"].id, owner_id)

    def clock_for_race(
        self, race_id: UUID, owner_id: UUID
    ) -> datetime | None:
        race = self.db.get(RaceSession, race_id)
        if race is None:
            return None
        meeting = self.db.get(Meeting, race.meeting_id)
        if meeting is None or meeting.source != SOURCE:
            return None
        self._require_development()
        self._require_owner(race, owner_id)
        virtual = self._virtual_time(race)
        self._release_due(race, virtual, owner_id)
        return virtual

    def state(self, race_id: UUID, owner_id: UUID) -> dict:
        self._require_development()
        race = self.db.get(RaceSession, race_id)
        self._require_owner(race, owner_id)
        virtual = self._virtual_time(race)
        self._release_due(race, virtual, owner_id)
        meeting = self.db.get(Meeting, race.meeting_id)
        metadata = self._metadata(race)
        return {
            "race": {
                "race_session_id": str(race.id),
                "meeting_id": str(meeting.id),
                "meeting_name": "Barcelona Grand Prix · Replay",
                "year": 2026,
                "session_name": race.name,
                "started_at": utc(race.started_at).isoformat(),
            },
            "source_race_session_id": metadata["source_race_id"],
            "virtual_time": virtual.isoformat(),
            "playing": metadata["playing"] and virtual < END,
            "speed": metadata["speed"],
            "events": [
                {
                    "key": key,
                    "label": label,
                    "starts_at": starts_at.isoformat(),
                    "results_at": results_at.isoformat(),
                    "state": (
                        "RESULT" if virtual >= results_at
                        else "LIVE" if virtual >= starts_at
                        else "UPCOMING"
                    ),
                }
                for key, label, starts_at, results_at in EVENTS
            ],
        }

    def control(
        self, race_id: UUID, owner_id: UUID, *, playing: bool, speed: int
    ) -> dict:
        self._require_development()
        if speed not in SPEEDS:
            raise ReplayError("Choose 60×, 600×, 3600×, or 14400×.")
        race = self.db.get(RaceSession, race_id)
        self._require_owner(race, owner_id)
        virtual = self._virtual_time(race)
        metadata = dict(self._metadata(race))
        metadata.update(
            base_virtual_at=virtual.isoformat(),
            anchor_real_at=self.now.isoformat(),
            playing=playing and virtual < END,
            speed=speed,
        )
        race.source_metadata = {"fantasy_replay": metadata}
        flag_modified(race, "source_metadata")
        self.db.commit()
        return self.state(race_id, owner_id)

    def _require_owner(self, race: RaceSession | None, owner_id: UUID) -> None:
        if (
            race is None
            or self._metadata(race).get("owner_profile_id") != str(owner_id)
        ):
            raise ReplayError("Fantasy replay was not found.")

    @staticmethod
    def _metadata(race: RaceSession | None) -> dict:
        return (race.source_metadata or {}).get("fantasy_replay", {}) if race else {}

    def _virtual_time(self, race: RaceSession) -> datetime:
        metadata = self._metadata(race)
        virtual = datetime.fromisoformat(metadata["base_virtual_at"])
        if metadata["playing"]:
            anchor = datetime.fromisoformat(metadata["anchor_real_at"])
            elapsed = max(0, (self.now - anchor).total_seconds())
            virtual += timedelta(seconds=elapsed * metadata["speed"])
        return min(utc(virtual), END)

    def _release_due(
        self, race: RaceSession, virtual: datetime, owner_id: UUID
    ) -> None:
        sessions = {
            session.session_identifier: session
            for session in self.db.scalars(
                select(RaceSession).where(RaceSession.meeting_id == race.meeting_id)
            )
        }
        source_id = UUID(self._metadata(race)["source_race_id"])
        source_results = self.db.scalars(
            select(SessionResult).where(SessionResult.race_session_id == source_id)
        ).all()
        driver_by_name = {
            driver.full_name: driver.id
            for driver in self.db.scalars(
                select(Driver).where(
                    Driver.id.in_([result.driver_id for result in source_results])
                )
            )
        }
        released = False
        if virtual >= EVENTS[0][3] and not self.db.scalar(
            select(SessionResult).where(
                SessionResult.race_session_id == sessions["FP1"].id
            )
        ):
            winner = driver_by_name.get("George Russell")
            if winner is None:
                raise ReplayError("George Russell is missing from the race grid.")
            self.db.add(SessionResult(
                race_session_id=sessions["FP1"].id,
                driver_id=winner,
                position=1,
            ))
            released = True
        if virtual >= EVENTS[1][3] and not self.db.scalar(
            select(SessionResult).where(
                SessionResult.race_session_id == sessions["Q"].id
            )
        ):
            hamilton = driver_by_name.get("Lewis Hamilton")
            russell = driver_by_name.get("George Russell")
            if hamilton is None or russell is None:
                raise ReplayError("Qualifying winners are missing from the grid.")
            self.db.add_all([
                SessionResult(
                    race_session_id=sessions["Q"].id,
                    driver_id=hamilton,
                    q1_time_ms=75625,
                ),
                SessionResult(
                    race_session_id=sessions["Q"].id,
                    driver_id=russell,
                    q1_time_ms=75717,
                    q2_time_ms=75228,
                    q3_time_ms=74679,
                ),
            ])
            released = True
        if virtual >= END:
            replay_results = self.db.scalars(
                select(SessionResult).where(
                    SessionResult.race_session_id == race.id
                )
            ).all()
            if all(row.position is None for row in replay_results):
                source_by_driver = {
                    row.driver_id: row for row in source_results
                }
                for row in replay_results:
                    source = source_by_driver[row.driver_id]
                    row.position = source.position
                    row.classified_position = source.classified_position
                    row.status = source.status
                    row.points = source.points
                self.db.add(Lap(
                    race_session_id=race.id,
                    driver_id=driver_by_name["Lewis Hamilton"],
                    lap_number=44,
                    lap_time_ms=80122,
                    is_accurate=True,
                    deleted=False,
                ))
                released = True
        if not released:
            return
        self.db.flush()
        fantasy = FantasyService(self.db, now=virtual)
        fantasy.score_available_questions(race.id)
        if virtual >= END:
            existing = {
                resolution.question_key
                for resolution in self.db.scalars(
                    select(FantasyQuestionResolution).where(
                        FantasyQuestionResolution.race_session_id == race.id
                    )
                )
            }
            if "RACE_DNF_DRIVERS" not in existing:
                retired = [
                    row.driver_id for row in source_results
                    if row.classified_position == "R"
                ]
                fantasy.set_question_resolution(
                    race_session_id=race.id,
                    question_key="RACE_DNF_DRIVERS",
                    payload=FantasyQuestionResolutionRequest(
                        status="RESOLVED",
                        actual_driver_ids=retired,
                        source_reference=f"session_results:{source_id}",
                    ),
                    resolved_by_profile_id=owner_id,
                )
            if "RACE_TOP_SPEED" not in existing:
                fantasy.set_question_resolution(
                    race_session_id=race.id,
                    question_key="RACE_TOP_SPEED",
                    payload=FantasyQuestionResolutionRequest(
                        status="NOT_SCORED",
                        source_reference="Speed-trap data unavailable",
                    ),
                    resolved_by_profile_id=owner_id,
                )
