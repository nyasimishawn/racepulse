from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import func, select

from app.api.v1.weekends import get_weekend_service
from app.core.security import AuthenticatedUser, get_current_user
from app.main import app
from app.models.user_profile import UserProfile
from app.models.weekend_download import WeekendDownload
from app.schemas.weekend import WeekendSchedule, WeekendSessionSchedule
from app.services.weekend_download_service import WeekendDownloadService


def test_selection_queues_one_shared_weekend_and_syncs_identity(
    api_client, db_session
):
    schedule = WeekendSchedule(
        year=2023,
        round_number=14,
        event_name="Italian Grand Prix",
        aliases=["monza"],
        sessions=[
            WeekendSessionSchedule(
                identifier="FP1",
                name="Practice 1",
                scheduled_at=datetime(2023, 9, 1, tzinfo=UTC),
            )
        ],
    )
    service = WeekendDownloadService(
        db_session,
        provider=SimpleNamespace(
            get_weekend_schedule=lambda **kwargs: schedule,
        ),
    )
    app.dependency_overrides[get_weekend_service] = lambda: service
    payload = {"year": 2023, "event_name": "Monza"}
    assert (
        api_client.post("/api/v1/weekends/select", json=payload).status_code
        == 401
    )
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        subject="new-keycloak-user",
        username="fan",
        email=None,
        email_verified=False,
        display_name="Fan",
        realm_roles=frozenset({"fan"}),
        groups=frozenset({"/Fans"}),
    )
    first = api_client.post("/api/v1/weekends/select", json=payload)
    second = api_client.post("/api/v1/weekends/select", json=payload)
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["job"]["id"] == second.json()["job"]["id"]
    assert (
        first.json()["sessions"][0]["stages"]["telemetry"]["status"]
        == "PENDING"
    )
    assert api_client.get(first.headers["Location"]).status_code == 200
    assert (
        db_session.scalar(select(func.count()).select_from(WeekendDownload))
        == 1
    )
    profile = db_session.scalar(select(UserProfile))
    assert profile.keycloak_groups == ["/Fans"]
    assert (
        api_client.post(first.headers["Location"] + "/retry").status_code
        == 403
    )


def test_weekend_input_and_unknown_id_are_validated(api_client):
    assert api_client.get("/api/v1/weekends/not-a-uuid").status_code == 422
    assert (
        api_client.get(
            "/api/v1/weekends/00000000-0000-0000-0000-000000000001"
        ).status_code
        == 404
    )
