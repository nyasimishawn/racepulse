from datetime import UTC, datetime, timedelta

from app.core.security import AuthenticatedUser, get_current_user
from app.main import app
from app.schemas.calendar import CalendarWeekendInput
from app.services.calendar_service import CalendarService
from app.services.alert_service import AlertService


def user(subject):
    return AuthenticatedUser(
        subject=subject,
        username=subject,
        email=None,
        email_verified=False,
        display_name=subject,
        realm_roles=frozenset({"fan"}),
        groups=frozenset(),
    )


def test_alert_preferences_feed_read_and_authorization(api_client, db_session):
    now = datetime.now(UTC)
    data = CalendarWeekendInput(
        year=now.year,
        round_number=1,
        event_name="Example Grand Prix",
        source_url="https://example.com/calendar",
        source_checked_at=now,
        change_reason="Verified",
        sessions=[
            {
                "identifier": "R",
                "name": "Race",
                "starts_at": now + timedelta(minutes=40),
            }
        ],
    )
    weekend = CalendarService(db_session).save(data, "editor")
    base = "/api/v1/alerts"
    assert api_client.get(base).status_code == 401
    app.dependency_overrides[get_current_user] = lambda: user("fan-one")
    updated = api_client.put(
        f"{base}/preferences/{weekend['id']}",
        json={
            "session_soon": True,
            "schedule_change": True,
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["fantasy_deadline"] is False
    assert len(api_client.get(f"{base}/preferences").json()) == 1
    assert api_client.get(base).json() == []
    AlertService(db_session, now=now + timedelta(minutes=11)).deliver_due()
    alerts = api_client.get(base).json()
    assert len(alerts) == 1 and alerts[0]["type"] == "SESSION_SOON"
    alert_id = alerts[0]["id"]
    app.dependency_overrides[get_current_user] = lambda: user("fan-two")
    assert api_client.get(base).json() == []
    assert api_client.post(f"{base}/{alert_id}/read").status_code == 404
    assert (
        api_client.delete(f"{base}/preferences/{weekend['id']}").status_code
        == 204
    )
    app.dependency_overrides[get_current_user] = lambda: user("fan-one")
    assert len(api_client.get(f"{base}/preferences").json()) == 1
    read = api_client.post(f"{base}/{alert_id}/read")
    assert read.status_code == 200 and read.json()["read_at"] is not None
    assert (
        api_client.post(f"{base}/{alert_id}/read").json()["read_at"]
        == read.json()["read_at"]
    )
    assert (
        api_client.delete(f"{base}/preferences/{weekend['id']}").status_code
        == 204
    )
    assert api_client.get(f"{base}/preferences").json() == []
    assert len(api_client.get(base).json()) == 1
    app.dependency_overrides.pop(get_current_user, None)
