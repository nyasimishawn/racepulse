from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.models.driver import Driver
from app.models.lap import Lap
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.team import Team
from app.models.user_profile import UserProfile
from app.core.rate_limit import registration_rate_limit
from app.main import app
from app.schemas.fantasy import FantasyQuestionSaveRequest
from app.services.fantasy_replay_generic_service import GenericFantasyReplayService
from app.services.fantasy_service import FantasyService


def test_imported_monza_replay_locks_and_scores_in_order(db_session, api_client):
    now = datetime(2026, 9, 27, 12, tzinfo=UTC)
    meeting = Meeting(source="FASTF1", year=2026, name="Italian Grand Prix")
    owner = UserProfile(keycloak_subject="monza-replay", display_name="Monza")
    db_session.add_all([meeting, owner])
    db_session.flush()
    starts = {
        "FP1": datetime(2026, 9, 4, 11, 30, tzinfo=UTC),
        "Q": datetime(2026, 9, 5, 14, tzinfo=UTC),
        "R": datetime(2026, 9, 6, 13, tzinfo=UTC),
    }
    sessions = {}
    for identifier, start in starts.items():
        session = RaceSession(
            meeting_id=meeting.id,
            name={"FP1": "Practice 1", "Q": "Qualifying", "R": "Race"}[identifier],
            session_identifier=identifier,
            session_type="Practice" if identifier == "FP1" else identifier,
            started_at=start,
        )
        db_session.add(session)
        db_session.flush()
        sessions[identifier] = session
    drivers = []
    for position in range(1, 4):
        driver = Driver(
            source="TEST", source_identifier=f"monza-driver-{position}",
            full_name=f"Driver {position}", driver_number=str(position),
        )
        team = Team(
            source="TEST", source_identifier=f"monza-team-{position}",
            name=f"Team {position}",
        )
        db_session.add_all([driver, team])
        db_session.flush()
        drivers.append(driver)
        for identifier, session in sessions.items():
            db_session.add(SessionResult(
                race_session_id=session.id, driver_id=driver.id, team_id=team.id,
                position=position,
                classified_position=str(position),
                q1_time_ms=75000 + position if identifier == "Q" else None,
            ))
    db_session.add(Lap(
        race_session_id=sessions["R"].id, driver_id=drivers[1].id,
        lap_number=10, lap_time_ms=80000, speed_st=320,
        is_accurate=True, deleted=False, fastf1_generated=False,
    ))
    db_session.commit()

    app.dependency_overrides[registration_rate_limit] = lambda: None
    guest = api_client.post("/api/v1/fantasy/guest-session").json()
    response = api_client.post(
        "/api/v1/fantasy/replay",
        headers={"X-Fantasy-Guest-Key": guest["guest_key"]},
        json={"race_session_id": str(sessions["R"].id)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["race"]["meeting_name"] == "Italian Grand Prix · Replay"

    service = GenericFantasyReplayService(db_session, now=now)
    created = service.create(owner.id, sessions["R"].id)
    race_id = UUID(created["race"]["race_session_id"])
    assert created["race"]["meeting_name"] == "Italian Grand Prix · Replay"
    assert created["source_race_session_id"] == str(sessions["R"].id)
    assert created["virtual_time"].startswith("2026-09-03")
    assert [event["key"] for event in created["events"]] == ["FP1", "Q", "R"]

    fantasy = FantasyService(db_session, now=datetime.fromisoformat(created["virtual_time"]))
    for key in ("FP1_P1", "Q1_FASTEST", "RACE_P1"):
        fantasy.save_question(
            user_profile_id=owner.id, race_session_id=race_id,
            question_key=key,
            payload=FantasyQuestionSaveRequest(driver_id=drivers[0].id),
        )
    for key in ("RACE_FASTEST_LAP", "RACE_TOP_SPEED"):
        fantasy.save_question(
            user_profile_id=owner.id, race_session_id=race_id,
            question_key=key,
            payload=FantasyQuestionSaveRequest(driver_id=drivers[1].id),
        )
    service.control(race_id, owner.id, playing=True, speed=14400)

    fp1 = GenericFantasyReplayService(db_session, now=now + timedelta(seconds=7))
    clock = fp1.clock_for_race(race_id, owner.id)
    assert FantasyService(db_session, now=clock).get_prediction(
        owner.id, race_id
    ).total_points == 2
    qualifying = GenericFantasyReplayService(db_session, now=now + timedelta(seconds=15))
    clock = qualifying.clock_for_race(race_id, owner.id)
    assert FantasyService(db_session, now=clock).get_prediction(
        owner.id, race_id
    ).total_points == 5
    finished = GenericFantasyReplayService(db_session, now=now + timedelta(seconds=21))
    clock = finished.clock_for_race(race_id, owner.id)
    prediction = FantasyService(db_session, now=clock).get_prediction(owner.id, race_id)
    assert prediction.total_points == 25
    assert all(event["state"] == "RESULT" for event in finished.state(
        race_id, owner.id
    )["events"])
    assert len(db_session.query(Lap).filter(Lap.race_session_id == race_id).all()) == 1
