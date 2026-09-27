"""Create an open Fantasy weekend for local pick testing.

Run from the repository root with ``python -m scripts.seed_fantasy_simulation``.
"""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import SessionLocal
from app.models.driver import Driver
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.team import Team


SOURCE = "FANTASY_SIM"
DRIVERS = (
    ("apex-1", "11", "Apex One", "apex"),
    ("apex-2", "12", "Apex Two", "apex"),
    ("vector-1", "21", "Vector One", "vector"),
    ("vector-2", "22", "Vector Two", "vector"),
    ("orbit-1", "31", "Orbit One", "orbit"),
    ("orbit-2", "32", "Orbit Two", "orbit"),
)
TEAMS = (
    ("apex", "Apex Racing"),
    ("vector", "Vector Racing"),
    ("orbit", "Orbit Racing"),
)


def seed_fantasy_simulation(
    db: Session,
    *,
    now: datetime | None = None,
    session_offsets: tuple[timedelta, timedelta, timedelta] | None = None,
) -> UUID:
    """Reuse an open demo race or create a new one with future locks."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    offsets = session_offsets or (
        timedelta(days=2),
        timedelta(days=3),
        timedelta(days=4),
    )
    if not (timedelta(0) < offsets[0] < offsets[1] < offsets[2]):
        raise ValueError("Session offsets must be increasing and in the future.")
    if session_offsets is None:
        existing = db.scalar(
            select(RaceSession)
            .join(Meeting, Meeting.id == RaceSession.meeting_id)
            .where(
                Meeting.source == SOURCE,
                RaceSession.session_identifier == "R",
                RaceSession.started_at > current + timedelta(days=3),
            )
            .order_by(RaceSession.started_at.desc())
        )
        if existing is not None:
            return existing.id

    teams = {}
    for identifier, name in TEAMS:
        team = db.scalar(
            select(Team).where(
                Team.source == SOURCE,
                Team.source_identifier == identifier,
            )
        )
        if team is None:
            team = Team(
                source=SOURCE,
                source_identifier=identifier,
                name=name,
            )
            db.add(team)
            db.flush()
        teams[identifier] = team

    drivers = []
    for identifier, number, name, team_identifier in DRIVERS:
        driver = db.scalar(
            select(Driver).where(
                Driver.source == SOURCE,
                Driver.source_identifier == identifier,
            )
        )
        if driver is None:
            driver = Driver(
                source=SOURCE,
                source_identifier=identifier,
                driver_number=number,
                full_name=name,
            )
            db.add(driver)
            db.flush()
        drivers.append((driver, teams[team_identifier]))

    race_start = current + offsets[2]
    meeting = Meeting(
        source=SOURCE,
        year=race_start.year,
        name=f"Fantasy Simulation {current:%Y%m%dT%H%M%S%f}",
        event_date=race_start,
    )
    db.add(meeting)
    db.flush()

    for name, identifier, session_type, offset in (
        ("Practice 1", "FP1", "Practice", offsets[0]),
        ("Qualifying", "Q", "Qualifying", offsets[1]),
        ("Race", "R", "Race", offsets[2]),
    ):
        session = RaceSession(
            meeting_id=meeting.id,
            name=name,
            session_identifier=identifier,
            session_type=session_type,
            source_metadata=(
                {"fantasy_simulation": "accelerated"}
                if session_offsets is not None
                else {}
            ),
            started_at=current + offset,
        )
        db.add(session)
        db.flush()
        if identifier == "R":
            race_id = session.id
            # Entrants have no results yet. These rows only link drivers to
            # teams for the pick options; they cannot resolve a question.
            db.add_all(
                SessionResult(
                    race_session_id=race_id,
                    driver_id=driver.id,
                    team_id=team.id,
                )
                for driver, team in drivers
            )

    db.commit()
    return race_id


def main() -> None:
    if settings.environment != "development":
        raise SystemExit("Fantasy simulation seeding requires development mode.")

    with SessionLocal() as db:
        race_id = seed_fantasy_simulation(db)
        race = db.get(RaceSession, race_id)
        meeting = db.get(Meeting, race.meeting_id)
        print(
            json.dumps(
                {
                    "race_session_id": str(race_id),
                    "meeting_name": meeting.name,
                    "year": meeting.year,
                    "race_locks_at": race.started_at.astimezone(UTC).isoformat(),
                    "prediction_path": (
                        f"/api/v1/fantasy/races/{race_id}/prediction"
                    ),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
