from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select

from app.models.driver import Driver
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.team import Team
from app.models.user_profile import UserProfile
from app.core.config import settings
from app.schemas.fantasy import FantasyQuestionSaveRequest
from app.services.fantasy_replay_service import (
    FantasyReplayService,
    ReplayError,
    R_START,
)
from app.services.fantasy_service import FantasyPredictionLockedError, FantasyService


def test_replay_is_disabled_outside_development(db_session, monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(ReplayError, match="development only"):
        FantasyReplayService(db_session).current(UUID(int=1))


def test_barcelona_replay_moves_from_thursday_to_scored_race(db_session):
    real_now = datetime(2026, 9, 27, 12, tzinfo=UTC)
    meeting = Meeting(source="TEST", year=2026, name="Barcelona Grand Prix")
    owner = UserProfile(keycloak_subject="replay-owner", display_name="Owner")
    other = UserProfile(keycloak_subject="replay-other", display_name="Other")
    db_session.add_all([meeting, owner, other])
    db_session.flush()
    source_race = RaceSession(
        meeting_id=meeting.id,
        name="Race",
        session_identifier="R",
        session_type="Race",
        started_at=R_START,
    )
    db_session.add(source_race)
    db_session.flush()
    ids = {}
    for position, name in enumerate(
        ("Lewis Hamilton", "George Russell", "Lando Norris", "Max Verstappen"),
        start=1,
    ):
        team = Team(
            source="TEST", source_identifier=f"team-{position}",
            name=f"Team {position}",
        )
        driver = Driver(
            source="TEST", source_identifier=f"driver-{position}",
            full_name=name, driver_number=str(position),
        )
        db_session.add_all([team, driver])
        db_session.flush()
        ids[name] = driver.id
        db_session.add(SessionResult(
            race_session_id=source_race.id,
            driver_id=driver.id,
            team_id=team.id,
            position=position if position < 4 else None,
            classified_position=str(position) if position < 4 else "R",
        ))
    db_session.commit()

    replay = FantasyReplayService(db_session, now=real_now).create(owner.id)
    race_id = UUID(replay["race"]["race_session_id"])
    assert replay["virtual_time"].startswith("2026-06-11T09:00:00")
    assert all(event["state"] == "UPCOMING" for event in replay["events"])
    assert FantasyReplayService(db_session, now=real_now).current(owner.id) is not None
    with pytest.raises(ReplayError, match="not found"):
        FantasyReplayService(db_session, now=real_now).state(race_id, other.id)

    clock = FantasyReplayService(db_session, now=real_now).clock_for_race(
        race_id, owner.id
    )
    fantasy = FantasyService(db_session, now=clock)
    for key, driver in (
        ("FP1_P1", "George Russell"),
        ("Q1_FASTEST", "Lewis Hamilton"),
        ("RACE_P1", "Lewis Hamilton"),
    ):
        fantasy.save_question(
            user_profile_id=owner.id,
            race_session_id=race_id,
            question_key=key,
            payload=FantasyQuestionSaveRequest(driver_id=ids[driver]),
        )
    FantasyReplayService(db_session, now=real_now).control(
        race_id, owner.id, playing=True, speed=3600
    )

    at_fp1 = FantasyReplayService(
        db_session, now=real_now + timedelta(seconds=26)
    )
    fp1_clock = at_fp1.clock_for_race(race_id, owner.id)
    fp1_prediction = FantasyService(db_session, now=fp1_clock).get_prediction(
        owner.id, race_id
    )
    assert fp1_prediction.total_points == 2
    with pytest.raises(FantasyPredictionLockedError):
        FantasyService(db_session, now=fp1_clock).save_question(
            user_profile_id=owner.id,
            race_session_id=race_id,
            question_key="FP1_P1",
            payload=FantasyQuestionSaveRequest(driver_id=ids["Lewis Hamilton"]),
        )

    at_q = FantasyReplayService(
        db_session, now=real_now + timedelta(seconds=53)
    )
    q_clock = at_q.clock_for_race(race_id, owner.id)
    assert FantasyService(db_session, now=q_clock).get_prediction(
        owner.id, race_id
    ).total_points == 5

    at_finish = FantasyReplayService(
        db_session, now=real_now + timedelta(seconds=79)
    )
    finish_clock = at_finish.clock_for_race(race_id, owner.id)
    finished = FantasyService(db_session, now=finish_clock).get_prediction(
        owner.id, race_id
    )
    assert finished.total_points == 15
    assert all(event["state"] == "RESULT" for event in at_finish.state(
        race_id, owner.id
    )["events"])
    original = db_session.scalars(
        select(SessionResult).where(
            SessionResult.race_session_id == source_race.id
        )
    ).all()
    assert len(original) == 4
    assert [row.position for row in original] == [1, 2, 3, None]
    restarted = FantasyReplayService(
        db_session, now=real_now + timedelta(minutes=2)
    ).create(owner.id)
    assert restarted["race"]["race_session_id"] != str(race_id)
    assert FantasyReplayService(
        db_session, now=real_now + timedelta(minutes=2)
    ).current(owner.id)["race"]["race_session_id"] == restarted[
        "race"
    ]["race_session_id"]
