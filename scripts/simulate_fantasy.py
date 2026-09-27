"""Run a short, time-locked Fantasy weekend in a development database.

Start:   python -m scripts.simulate_fantasy start --minutes 2
Release: python -m scripts.simulate_fantasy release RACE_SESSION_ID
"""

import argparse
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import SessionLocal
from app.models.driver import Driver
from app.models.fantasy import FantasyQuestionResolution
from app.models.lap import Lap
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.user_profile import UserProfile
from app.schemas.fantasy import FantasyQuestionResolutionRequest
from app.services.fantasy_service import FantasyService
from scripts.seed_fantasy_simulation import DRIVERS, SOURCE, seed_fantasy_simulation


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _sessions(db: Session, race_id: UUID) -> dict[str, RaceSession]:
    race = db.get(RaceSession, race_id)
    meeting = db.get(Meeting, race.meeting_id) if race else None
    if (
        race is None
        or meeting is None
        or meeting.source != SOURCE
        or race.session_identifier != "R"
    ):
        raise ValueError("Race ID must belong to a Fantasy simulation.")
    sessions = {
        session.session_identifier: session
        for session in db.scalars(
            select(RaceSession).where(RaceSession.meeting_id == meeting.id)
        )
    }
    if set(sessions) != {"FP1", "Q", "R"} or any(
        session.source_metadata.get("fantasy_simulation") != "accelerated"
        or session.started_at is None
        for session in sessions.values()
    ):
        raise ValueError("Race ID is not an accelerated Fantasy simulation.")
    return sessions


def start_simulation(
    db: Session, *, now: datetime | None = None, minutes: int = 2
) -> dict[str, object]:
    if minutes < 1:
        raise ValueError("Minutes between locks must be at least one.")
    current = _utc(now or datetime.now(UTC))
    race_id = seed_fantasy_simulation(
        db,
        now=current,
        session_offsets=tuple(
            timedelta(minutes=minutes * index) for index in (1, 2, 3)
        ),
    )
    sessions = _sessions(db, race_id)
    return {
        "race_session_id": str(race_id),
        "prediction_path": f"/api/v1/fantasy/races/{race_id}/prediction",
        "locks_at": {
            key: _utc(session.started_at).isoformat()
            for key, session in sessions.items()
        },
    }


def release_due_results(
    db: Session, race_id: UUID, *, now: datetime | None = None
) -> dict[str, object]:
    """Publish known outcomes only after their UTC lock, then use normal scoring."""
    current = _utc(now or datetime.now(UTC))
    sessions = _sessions(db, race_id)
    driver_ids = {
        driver.source_identifier: driver.id
        for driver in db.scalars(
            select(Driver).where(
                Driver.source == SOURCE,
                Driver.source_identifier.in_([row[0] for row in DRIVERS]),
            )
        )
    }
    if set(driver_ids) != {row[0] for row in DRIVERS}:
        raise ValueError("Simulation drivers are incomplete.")

    released = []
    for key in ("FP1", "Q", "R"):
        session = sessions[key]
        if current < _utc(session.started_at):
            continue
        if key == "FP1":
            existing = db.scalar(
                select(SessionResult).where(
                    SessionResult.race_session_id == session.id
                )
            )
            if existing is None:
                order = (
                    "apex-1", "vector-1", "orbit-1",
                    "apex-2", "vector-2", "orbit-2",
                )
                db.add_all(
                    SessionResult(
                        race_session_id=session.id,
                        driver_id=driver_ids[identifier],
                        position=position,
                    )
                    for position, identifier in enumerate(order, start=1)
                )
                released.append(key)
        elif key == "Q":
            existing = db.scalar(
                select(SessionResult).where(
                    SessionResult.race_session_id == session.id
                )
            )
            if existing is None:
                times = {
                    "apex-1": (90000, 90500, 91000),
                    "apex-2": (91500, 91600, 91900),
                    "vector-1": (90700, 89900, 91200),
                    "vector-2": (92000, 91800, 92100),
                    "orbit-1": (91100, 90800, 89800),
                    "orbit-2": (92500, 92300, 92700),
                }
                db.add_all(
                    SessionResult(
                        race_session_id=session.id,
                        driver_id=driver_ids[identifier],
                        q1_time_ms=values[0],
                        q2_time_ms=values[1],
                        q3_time_ms=values[2],
                    )
                    for identifier, values in times.items()
                )
                released.append(key)
        else:
            results = db.scalars(
                select(SessionResult).where(
                    SessionResult.race_session_id == session.id
                )
            ).all()
            if len(results) != len(DRIVERS):
                raise ValueError("Simulation race entrants are incomplete.")
            if all(result.position is None for result in results):
                positions = {
                    "vector-1": 1, "apex-1": 2, "orbit-1": 3,
                    "apex-2": 4, "vector-2": 5,
                }
                identifiers = {
                    driver_id: identifier
                    for identifier, driver_id in driver_ids.items()
                }
                for result in results:
                    identifier = identifiers[result.driver_id]
                    result.position = positions.get(identifier)
                    result.classified_position = (
                        str(result.position) if result.position else "NC"
                    )
                    result.status = (
                        "Finished" if result.position else "Retired"
                    )
                db.add_all(
                    (
                        Lap(
                            race_session_id=session.id,
                            driver_id=driver_ids["apex-2"],
                            lap_number=1,
                            lap_time_ms=89000,
                            speed_st=Decimal("310"),
                            is_accurate=True,
                            deleted=False,
                        ),
                        Lap(
                            race_session_id=session.id,
                            driver_id=driver_ids["vector-2"],
                            lap_number=1,
                            lap_time_ms=92000,
                            speed_st=Decimal("340"),
                            is_accurate=True,
                            deleted=False,
                        ),
                    )
                )
                released.append(key)

    db.flush()
    fantasy = FantasyService(db, now=current)
    score = fantasy.score_available_questions(race_id)
    finalized_groups = None
    if current >= _utc(sessions["R"].started_at):
        dnf = db.scalar(
            select(FantasyQuestionResolution).where(
                FantasyQuestionResolution.race_session_id == race_id,
                FantasyQuestionResolution.question_key == "RACE_DNF_DRIVERS",
            )
        )
        if dnf is None:
            editor = UserProfile(
                keycloak_subject=f"fantasy-simulation-editor-{race_id}",
                display_name="Fantasy Simulation Editor",
            )
            db.add(editor)
            db.flush()
            fantasy.set_question_resolution(
                race_session_id=race_id,
                question_key="RACE_DNF_DRIVERS",
                payload=FantasyQuestionResolutionRequest(
                    status="RESOLVED",
                    actual_driver_ids=[driver_ids["orbit-2"]],
                    source_reference="Known simulation classification",
                ),
                resolved_by_profile_id=editor.id,
            )
        finalized_groups = fantasy.finalize_weekend(
            race_id
        ).finalized_group_count
    return {
        "race_session_id": str(race_id),
        "released_sessions": released,
        "resolved_question_keys": score.resolved_question_keys,
        "pending_question_keys": (
            [] if finalized_groups is not None else score.pending_question_keys
        ),
        "finalized_group_count": finalized_groups,
    }


def main() -> None:
    if settings.environment != "development":
        raise SystemExit("Fantasy simulation requires development mode.")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="Create a short, open weekend")
    start.add_argument("--minutes", type=int, default=2)
    release = commands.add_parser("release", help="Publish results due now")
    release.add_argument("race_session_id", type=UUID)
    args = parser.parse_args()
    with SessionLocal() as db:
        try:
            if args.command == "start":
                result = start_simulation(db, minutes=args.minutes)
            else:
                result = release_due_results(db, args.race_session_id)
        except ValueError as error:
            raise SystemExit(str(error)) from error
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
