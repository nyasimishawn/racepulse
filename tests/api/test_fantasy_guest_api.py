from datetime import UTC, datetime, timedelta

from app.core.config import settings
from app.core.fantasy_guest import _guest_subject
from app.core.rate_limit import (
    expensive_request_rate_limit,
    registration_rate_limit,
)
from app.main import app
from app.models.driver import Driver
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.team import Team
from app.models.user_profile import UserProfile


def seed_race(db):
    meeting = Meeting(source="TEST", year=2026, name="Guest Grand Prix")
    db.add(meeting)
    db.flush()
    race = RaceSession(
        meeting_id=meeting.id,
        name="Race",
        session_identifier="R",
        session_type="Race",
        started_at=datetime.now(UTC) + timedelta(days=2),
    )
    driver = Driver(
        source="TEST",
        source_identifier="guest-driver",
        driver_number="1",
        full_name="Guest Driver",
    )
    team = Team(
        source="TEST",
        source_identifier="guest-team",
        name="Guest Team",
    )
    db.add_all([race, driver, team])
    db.flush()
    db.add(
        SessionResult(
            race_session_id=race.id,
            driver_id=driver.id,
            team_id=team.id,
            position=1,
        )
    )
    db.commit()
    return race, driver


def test_guest_picks_persist_and_are_isolated(api_client, db_session):
    app.dependency_overrides[registration_rate_limit] = lambda: None
    race, driver = seed_race(db_session)
    base = f"/api/v1/fantasy/races/{race.id}"
    assert api_client.get("/api/v1/fantasy/races").status_code == 401
    assert api_client.get(f"{base}/prediction").status_code == 401

    first = api_client.post("/api/v1/fantasy/guest-session")
    second = api_client.post("/api/v1/fantasy/guest-session")
    assert first.status_code == second.status_code == 201
    first_key = first.json()["guest_key"]
    second_key = second.json()["guest_key"]
    assert first_key != second_key
    first_headers = {"X-Fantasy-Guest-Key": first_key}
    second_headers = {"X-Fantasy-Guest-Key": second_key}
    assert (
        api_client.get(
            "/api/v1/fantasy/races", headers=first_headers
        ).status_code
        == 200
    )

    saved = api_client.put(
        f"{base}/questions/RACE_P1",
        headers=first_headers,
        json={"driver_id": str(driver.id)},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["question"]["is_answered"] is True
    assert (
        api_client.get(f"{base}/prediction", headers=first_headers).json()[
            "prediction_id"
        ]
        is not None
    )
    other = api_client.get(f"{base}/prediction", headers=second_headers)
    assert other.status_code == 200
    assert other.json()["prediction_id"] is None
    created_group = api_client.post(
        "/api/v1/fantasy/groups",
        headers=first_headers,
        json={"name": "Guest test group"},
    )
    assert created_group.status_code == 201
    assert (
        len(
            api_client.get(
                "/api/v1/fantasy/groups", headers=first_headers
            ).json()
        )
        == 1
    )
    assert (
        api_client.get("/api/v1/fantasy/groups", headers=second_headers).json()
        == []
    )
    assert (
        api_client.get(
            "/api/v1/fantasy/me/dashboard", headers=first_headers
        ).status_code
        == 200
    )
    assert (
        api_client.get(
            f"{base}/prediction", headers={"X-Fantasy-Guest-Key": "invalid"}
        ).status_code
        == 401
    )

    profile = (
        db_session.query(UserProfile)
        .filter_by(keycloak_subject=_guest_subject(first_key))
        .one()
    )
    profile.is_active = False
    db_session.commit()
    assert (
        api_client.get(f"{base}/prediction", headers=first_headers).status_code
        == 403
    )


def test_guest_mode_does_not_open_editor_routes_or_production(
    api_client, monkeypatch
):
    app.dependency_overrides[registration_rate_limit] = lambda: None
    app.dependency_overrides[expensive_request_rate_limit] = lambda: None
    from uuid import uuid4

    race_id = uuid4()
    assert (
        api_client.post(f"/api/v1/fantasy/races/{race_id}/score").status_code
        == 401
    )
    monkeypatch.setattr(settings, "environment", "production")
    assert api_client.post("/api/v1/fantasy/guest-session").status_code == 404
    assert (
        api_client.get(
            "/api/v1/fantasy/me/dashboard",
            headers={
                "X-Fantasy-Guest-Key": "A" * 43,
            },
        ).status_code
        == 401
    )
