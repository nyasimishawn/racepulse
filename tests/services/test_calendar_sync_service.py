from datetime import UTC, datetime
import json

import httpx
from sqlalchemy import func, select

from app.models.calendar import CalendarRevision, CalendarWeekend
from app.models.alert import Alert
from app.models.user_profile import UserProfile
from app.services.alert_service import AlertService
from app.schemas.calendar import CalendarWeekendInput
from app.services.calendar_service import CalendarService
from app.services.calendar_sync_service import BASE, parse_event, sync_calendar

URL = BASE + "/en/racing/2026/spain"


def page(start="2026-09-13T13:00:00Z"):
    event = {
        "@id": URL,
        "subEvent": [
            {
                "name": "Race - Spanish Grand Prix",
                "startDate": start,
                "eventStatus": "https://schema.org/EventScheduled",
            },
        ],
    }
    return (
        '<script type="application/ld+json">'
        + json.dumps(event)
        + '</script>"meetingNumber":"14"'
    )


def test_sync_idempotency_failure_and_editor_override(db_session):
    broken = False

    def handler(request):
        if request.url.path == "/en/racing/2026":
            return httpx.Response(
                200, text='<a href="/en/racing/2026/spain">Spain</a>'
            )
        return httpx.Response(502 if broken else 200, text=page())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert sync_calendar(db_session, 2026, client)["created"] == 1
        row = db_session.scalar(select(CalendarWeekend))
        profile = UserProfile(keycloak_subject="calendar-follower")
        db_session.add(profile)
        db_session.commit()
        AlertService(db_session).set_preference(
            profile.id, row, session_soon=True,
            fantasy_deadline=False, schedule_change=True,
        )
        assert sync_calendar(db_session, 2026, client)["unchanged"] == 1
        assert db_session.scalars(select(Alert)).all() == []
        assert (
            db_session.scalar(
                select(func.count()).select_from(CalendarRevision)
            )
            == 1
        )
        row = db_session.scalar(select(CalendarWeekend))
        broken = True
        assert sync_calendar(db_session, 2026, client)["status"] == "PARTIAL"
        assert row.schedule["status"] == "SCHEDULED"
        data = CalendarWeekendInput(**{**row.schedule, "status": "POSTPONED"})
        CalendarService(db_session).save(data, "editor", row.id, row.version)
        broken = False
        assert sync_calendar(db_session, 2026, client)["editor_owned"] == 1
        assert row.schedule["status"] == "POSTPONED"


def test_parser_rejects_naive_dates():
    import pytest

    with pytest.raises(ValueError):
        parse_event(page("2026-09-13T13:00:00"), URL, 2026, datetime.now(UTC))


def test_removed_event_preserved_for_review(db_session):
    def handler(request):
        if request.url.path == "/en/racing/2026":
            return httpx.Response(
                200, text='<a href="/en/racing/2026/spain">Spain</a>'
            )
        return httpx.Response(200, text=page())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sync_calendar(db_session, 2026, client)
        row = db_session.scalar(select(CalendarWeekend))
        row.sync_key = BASE + "/en/racing/2026/removed"
        db_session.commit()
        result = sync_calendar(db_session, 2026, client)
        assert str(row.id) in result["missing_from_source"]
        assert db_session.get(CalendarWeekend, row.id) is not None


def test_once_worker_signals_partial_failure(monkeypatch):
    from contextlib import nullcontext
    import pytest
    from app.workers import calendar_sync_worker

    monkeypatch.setattr("sys.argv", ["calendar-sync", "--once"])
    monkeypatch.setattr(
        calendar_sync_worker, "SessionLocal", lambda: nullcontext(None)
    )
    monkeypatch.setattr(
        calendar_sync_worker,
        "sync_calendar",
        lambda *args: {"status": "PARTIAL"},
    )
    with pytest.raises(SystemExit) as error:
        calendar_sync_worker.main()
    assert error.value.code == 1
