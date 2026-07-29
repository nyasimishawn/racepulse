from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import AuthenticatedUser, get_current_user
from app.main import app
from app.models.driver import Driver
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.team import Team


@pytest.fixture()
def fantasy_client(api_client: TestClient) -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: (
        AuthenticatedUser(
            subject="fantasy-api-player",
            username="fantasy-player",
            email="fantasy@example.com",
            email_verified=True,
            display_name="Fantasy Player",
            realm_roles=frozenset({"fan"}),
        )
    )

    return api_client


def seed_future_race(
    db_session: Session,
) -> tuple[RaceSession, Driver, Team]:
    meeting = Meeting(
        source="TEST",
        year=2026,
        name="Fantasy API Grand Prix",
    )
    db_session.add(meeting)
    db_session.flush()

    race = RaceSession(
        meeting_id=meeting.id,
        name="Race",
        session_identifier="R",
        session_type="Race",
        started_at=datetime.now(UTC) + timedelta(days=2),
    )
    driver = Driver(
        source="TEST",
        source_identifier="fantasy-api-driver",
        driver_number="1",
        full_name="Fantasy Driver",
    )
    team = Team(
        source="TEST",
        source_identifier="fantasy-api-team",
        name="Fantasy Team",
    )

    db_session.add_all([race, driver, team])
    db_session.flush()

    db_session.add(
        SessionResult(
            race_session_id=race.id,
            driver_id=driver.id,
            team_id=team.id,
            position=1,
        )
    )
    db_session.commit()

    return race, driver, team


def test_fantasy_prediction_and_community_routes(
    fantasy_client: TestClient,
    db_session: Session,
) -> None:
    race, driver, team = seed_future_race(db_session)

    prediction_response = fantasy_client.get(
        f"/api/v1/fantasy/races/{race.id}/prediction"
    )

    assert prediction_response.status_code == 200

    prediction = prediction_response.json()
    question_keys = {
        question["key"] for question in prediction["questions"]
    }

    assert prediction["race"]["race_session_id"] == str(race.id)
    assert "RACE_P1" in question_keys
    assert prediction["drivers"][0]["id"] == str(driver.id)

    save_response = fantasy_client.put(
        f"/api/v1/fantasy/races/{race.id}/prediction",
        json={
            "answers": [
                {
                    "question_key": "RACE_P1",
                    "driver_id": str(driver.id),
                },
                {
                    "question_key": "RACE_WINNING_TEAM",
                    "team_id": str(team.id),
                },
            ]
        },
    )

    assert save_response.status_code == 200
    assert save_response.json()["prediction_id"]

    community_response = fantasy_client.get(
        f"/api/v1/fantasy/races/{race.id}/questions/"
        "RACE_P1/community"
    )

    assert community_response.status_code == 200
    assert community_response.json()["total_predictions"] == 1
    assert (
        community_response.json()["options"][0]["option_id"]
        == str(driver.id)
    )