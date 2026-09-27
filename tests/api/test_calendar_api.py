from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core.security import AuthenticatedUser, get_current_user
from app.main import app
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.schemas.calendar import CalendarWeekendInput
from app.services.calendar_service import CalendarService
from app.services.fantasy_service import FantasyService


def payload():
    return {
        "year": 2026,
        "round_number": 1,
        "event_name": "Test Grand Prix",
        "source_url": "https://example.com/test-schedule",
        "source_checked_at": datetime.now(UTC).isoformat(),
        "change_reason": "Initial verified schedule",
        "sessions": [
            {
                "identifier": "R",
                "name": "Race",
                "starts_at": (
                    datetime.now(UTC) + timedelta(days=3)
                ).isoformat(),
            }
        ],
    }


def editor():
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        subject="calendar-editor",
        username="editor",
        email=None,
        email_verified=False,
        display_name="Editor",
        realm_roles=frozenset({"editor"}),
        groups=frozenset(),
    )


def test_calendar_independent_of_imports_and_audited_updates(api_client):
    assert api_client.get("/api/v1/weekends/next").json() is None
    data = payload()
    assert api_client.post("/api/v1/calendar", json=data).status_code == 401
    editor()
    created = api_client.post("/api/v1/calendar", json=data)
    assert created.status_code == 201, created.text
    row = created.json()
    assert row["sessions"][0]["imported"] is False
    assert api_client.get("/api/v1/weekends/next").json()["id"] == row["id"]
    assert len(api_client.get("/api/v1/calendar?year=2026").json()) == 1
    assert (
        api_client.get(f"/api/v1/weekends/{row['id']}/overview").status_code
        == 200
    )
    data["status"] = "CANCELLED"
    data["change_reason"] = "Cancellation confirmed"
    url = f"/api/v1/calendar/{row['id']}?version=1"
    updated = api_client.put(url, json=data)
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2
    assert api_client.put(url, json=data).status_code == 409
    assert api_client.get("/api/v1/weekends/next").json() is None
    revisions = api_client.get(f"/api/v1/calendar/{row['id']}/history").json()
    assert [r["version"] for r in revisions] == [1, 2]
    assert revisions[0]["schedule"]["status"] == "SCHEDULED"


def test_calendar_validation(api_client):
    editor()
    data = payload()
    data["sessions"][0]["starts_at"] = "2026-09-10T14:00:00"
    assert api_client.post("/api/v1/calendar", json=data).status_code == 422
    data = payload()
    data["sessions"] *= 2
    assert api_client.post("/api/v1/calendar", json=data).status_code == 422
    assert (
        api_client.get(f"/api/v1/weekends/{uuid4()}/overview").status_code
        == 404
    )


def test_fantasy_reschedule_suspend_and_keep_locked_predictions(
    db_session, monkeypatch
):
    now = datetime.now(UTC)
    meeting = Meeting(source="TEST", year=2026, name="Test Grand Prix")
    db_session.add(meeting)
    db_session.flush()
    race = RaceSession(
        meeting_id=meeting.id,
        name="Race",
        session_identifier="R",
        session_type="Race",
        started_at=now + timedelta(days=3),
    )
    db_session.add(race)
    db_session.commit()
    data = payload()
    data["meeting_id"] = str(meeting.id)
    service = CalendarService(db_session)
    row = service.save(CalendarWeekendInput(**data), "editor")
    assert row["sessions"][0]["race_session_id"] == race.id
    fantasy = FantasyService(db_session, now=now)
    context = fantasy._load_race_context(race.id)
    original = fantasy._questions_for_context(context)
    new_time = now + timedelta(days=4)
    data["sessions"][0]["starts_at"] = new_time.isoformat()
    row = service.save(
        CalendarWeekendInput(**data), "editor", row["id"], row["version"]
    )
    questions = fantasy._questions_for_context(context)
    assert questions and all(q.locks_at == new_time for q in questions)
    assert original[0].locks_at != questions[0].locks_at
    # Advance past the deadline, suspend, and then announce a later date.
    fantasy._fixed_now = now + timedelta(days=5)
    fantasy._questions_for_context(context)

    class LaterDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fantasy._fixed_now

    monkeypatch.setattr(
        "app.services.calendar_service.datetime", LaterDateTime
    )
    data["status"] = "POSTPONED"
    row = service.save(
        CalendarWeekendInput(**data), "editor", row["id"], row["version"]
    )
    assert all(
        q.locks_at is None for q in fantasy._questions_for_context(context)
    )
    data["status"] = "SCHEDULED"
    data["sessions"][0]["starts_at"] = (now + timedelta(days=6)).isoformat()
    service.save(
        CalendarWeekendInput(**data), "editor", row["id"], row["version"]
    )
    assert all(
        fantasy._question_is_locked(q)
        for q in fantasy._questions_for_context(context)
    )
