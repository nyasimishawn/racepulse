from datetime import UTC, datetime

from app.core.rate_limit import registration_rate_limit
from app.main import app
from app.models.driver import Driver
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.team import Team


def test_guest_can_start_private_barcelona_replay(api_client, db_session):
    app.dependency_overrides[registration_rate_limit] = lambda: None
    meeting = Meeting(source="TEST", year=2026, name="Barcelona Grand Prix")
    db_session.add(meeting)
    db_session.flush()
    race = RaceSession(
        meeting_id=meeting.id,
        name="Race",
        session_identifier="R",
        session_type="Race",
        started_at=datetime(2026, 6, 14, 13, tzinfo=UTC),
    )
    db_session.add(race)
    db_session.flush()
    for position, name in enumerate(
        ("Lewis Hamilton", "George Russell", "Lando Norris"), start=1
    ):
        driver = Driver(
            source="TEST", source_identifier=f"replay-{position}",
            full_name=name, driver_number=str(position),
        )
        team = Team(
            source="TEST", source_identifier=f"replay-team-{position}",
            name=f"Replay Team {position}",
        )
        db_session.add_all([driver, team])
        db_session.flush()
        db_session.add(SessionResult(
            race_session_id=race.id,
            driver_id=driver.id,
            team_id=team.id,
            position=position,
            classified_position=str(position),
        ))
    db_session.commit()

    first_key = api_client.post(
        "/api/v1/fantasy/guest-session"
    ).json()["guest_key"]
    second_key = api_client.post(
        "/api/v1/fantasy/guest-session"
    ).json()["guest_key"]
    first = {"X-Fantasy-Guest-Key": first_key}
    second = {"X-Fantasy-Guest-Key": second_key}
    created = api_client.post("/api/v1/fantasy/replay", headers=first)
    assert created.status_code == 200, created.text
    replay = created.json()
    replay_id = replay["race"]["race_session_id"]
    assert replay["virtual_time"].startswith("2026-06-11")
    assert replay["playing"] is False
    assert (
        api_client.get("/api/v1/fantasy/replay", headers=first).json()
        ["race"]["race_session_id"] == replay_id
    )
    assert api_client.get("/api/v1/fantasy/replay", headers=second).json() is None
    assert api_client.get(
        f"/api/v1/fantasy/races/{replay_id}/prediction", headers=second
    ).status_code == 404
    prediction = api_client.get(
        f"/api/v1/fantasy/races/{replay_id}/prediction", headers=first
    )
    assert prediction.status_code == 200
    assert all(
        question["state"] == "OPEN"
        for question in prediction.json()["questions"]
    )
    controlled = api_client.put(
        f"/api/v1/fantasy/replay/{replay_id}",
        headers=first,
        json={"playing": True, "speed": 3600},
    )
    assert controlled.status_code == 200
    assert controlled.json()["playing"] is True
    assert api_client.put(
        f"/api/v1/fantasy/replay/{replay_id}",
        headers=first,
        json={"playing": True, "speed": 1_000_000},
    ).status_code == 422
    races = api_client.get("/api/v1/fantasy/races", headers=second).json()
    assert replay_id not in {entry["race_session_id"] for entry in races}
