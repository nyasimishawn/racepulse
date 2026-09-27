from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import AuthenticatedUser, get_current_user
from app.main import app
from app.models.driver import Driver
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.team import Team


def _seed_public_entities(
    db_session: Session,
) -> tuple[Driver, Team, Meeting, RaceSession]:
    meeting = Meeting(
        source="FASTF1",
        year=2026,
        name="API Profile Grand Prix",
        event_date=datetime(2026, 4, 1, tzinfo=UTC),
    )
    driver = Driver(
        source="FASTF1",
        source_identifier="api-profile-driver",
        driver_number="11",
        full_name="API Profile Driver",
    )
    team = Team(
        source="FASTF1",
        source_identifier="api-profile-team",
        name="API Profile Team",
    )
    db_session.add_all([meeting, driver, team])
    db_session.flush()

    race = RaceSession(
        meeting_id=meeting.id,
        name="Race",
        session_identifier="R",
        session_type="Race",
        started_at=datetime(2026, 4, 1, 14, tzinfo=UTC),
    )
    db_session.add(race)
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
    return driver, team, meeting, race


def _authenticate_as(role: str) -> None:
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        subject=f"{role}-profile-editor",
        username=role,
        email=f"{role}@example.com",
        email_verified=True,
        display_name=f"{role.title()} User",
        realm_roles=frozenset({role}),
    )


def test_profile_reads_are_public_and_history_declares_coverage(
    api_client: TestClient,
    db_session: Session,
) -> None:
    driver, team, _meeting, _race = _seed_public_entities(db_session)

    driver_response = api_client.get(f"/api/v1/drivers/{driver.id}")
    history_response = api_client.get(
        f"/api/v1/teams/{team.id}/history"
    )

    assert driver_response.status_code == 200
    assert driver_response.json()["biography"] is None
    assert driver_response.json()["recorded_teams"][0]["id"] == str(team.id)
    team_response = api_client.get(f"/api/v1/teams/{team.id}")
    assert team_response.json()["recorded_drivers"][0]["id"] == str(driver.id)
    assert history_response.status_code == 200
    assert history_response.json()["totals"]["wins"] == 1
    assert history_response.json()["coverage"][
        "complete_historical_coverage"
    ] is False


def test_profile_and_editorial_writes_require_editor_role(
    api_client: TestClient,
    db_session: Session,
) -> None:
    driver, _team, meeting, race = _seed_public_entities(db_session)
    profile_payload = {
        "biography": "A sourced profile biography.",
        "source_url": "https://www.fia.com/api-profile-driver",
        "publisher": "FIA",
        "published_at": "2026-04-01T12:00:00Z",
        "confidence": "HIGH",
    }

    _authenticate_as("fan")
    denied = api_client.put(
        f"/api/v1/drivers/{driver.id}/profile",
        json=profile_payload,
    )

    assert denied.status_code == 403

    _authenticate_as("editor")
    profile_response = api_client.put(
        f"/api/v1/drivers/{driver.id}/profile",
        json=profile_payload,
    )
    editorial_response = api_client.post(
        "/api/v1/editorial/updates",
        json={
            "update_type": "RACE_CONTROL",
            "publication_status": "PUBLISHED",
            "title": "Published FIA update",
            "body": "A sourced curated race-control-related update.",
            "meeting_id": str(meeting.id),
            "race_session_id": str(race.id),
            "driver_id": str(driver.id),
            "source_url": "https://www.fia.com/api-race-control",
            "publisher": "FIA",
            "published_at": "2026-04-01T12:00:00Z",
            "confidence": "HIGH",
        },
    )
    draft_response = api_client.post(
        "/api/v1/editorial/updates",
        json={
            "update_type": "TEAM_UPDATE",
            "title": "Draft team update",
            "body": "An editor can review this before publication.",
            "source_url": "https://www.example.com/api-draft",
            "publisher": "Example Team",
            "published_at": "2026-04-01T12:00:00Z",
        },
    )
    editor_drafts = api_client.get(
        "/api/v1/editorial/updates/manage?publication_status=DRAFT"
    )

    assert profile_response.status_code == 200
    assert profile_response.json()["biography"] == profile_payload["biography"]
    assert editorial_response.status_code == 201
    assert draft_response.status_code == 201
    assert editor_drafts.status_code == 200
    assert editor_drafts.json()[0]["id"] == draft_response.json()["id"]

    app.dependency_overrides.pop(get_current_user, None)
    public_updates = api_client.get("/api/v1/editorial/updates")
    update_id = UUID(editorial_response.json()["id"])
    public_detail = api_client.get(f"/api/v1/editorial/updates/{update_id}")

    assert public_updates.status_code == 200
    assert public_updates.json()[0]["id"] == str(update_id)
    assert public_detail.status_code == 200


def test_editor_can_update_and_delete_an_editorial_update(
    api_client: TestClient,
    db_session: Session,
) -> None:
    _driver, _team, _meeting, _race = _seed_public_entities(db_session)
    _authenticate_as("editor")
    created = api_client.post(
        "/api/v1/editorial/updates",
        json={
            "update_type": "PIRELLI_UPDATE",
            "title": "Initial tyre note",
            "body": "Draft source-backed tyre allocation note.",
            "source_url": "https://www.pirelli.com/example-note",
            "publisher": "Pirelli",
            "published_at": "2026-04-01T12:00:00Z",
        },
    )
    update_id = UUID(created.json()["id"])

    updated = api_client.patch(
        f"/api/v1/editorial/updates/{update_id}",
        json={
            "title": "Published tyre note",
            "publication_status": "PUBLISHED",
        },
    )
    deleted = api_client.delete(f"/api/v1/editorial/updates/{update_id}")

    assert created.status_code == 201
    assert updated.status_code == 200
    assert updated.json()["publication_status"] == "PUBLISHED"
    assert updated.json()["title"] == "Published tyre note"
    assert deleted.status_code == 204

    app.dependency_overrides.pop(get_current_user, None)
    missing = api_client.get(f"/api/v1/editorial/updates/{update_id}")
    assert missing.status_code == 404
