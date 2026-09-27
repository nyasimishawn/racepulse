from datetime import UTC, datetime, timedelta

import pytest

from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.schemas.calendar import CalendarWeekendInput
from app.services.calendar_service import CalendarService
from app.services.fantasy_service import FantasyService


@pytest.mark.parametrize("snapshot_exists", [False, True])
@pytest.mark.parametrize("suspend", [False, True])
def test_elapsed_deadline_cannot_reopen_without_intervening_reads(
    db_session, monkeypatch, snapshot_exists, suspend
):
    base = datetime(2026, 1, 1, tzinfo=UTC)
    clock = [base]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    monkeypatch.setattr("app.services.calendar_service.datetime", Clock)
    meeting = Meeting(source="TEST", year=2026, name="Test")
    db_session.add(meeting)
    db_session.flush()
    deadline = base + timedelta(days=1)
    race = RaceSession(
        meeting_id=meeting.id,
        name="Race",
        session_identifier="R",
        session_type="Race",
        started_at=deadline,
    )
    db_session.add(race)
    db_session.commit()
    data = dict(
        year=2026,
        round_number=1,
        event_name="Test",
        meeting_id=meeting.id,
        source_url="https://example.com/schedule",
        source_checked_at=base,
        change_reason="Verified",
        sessions=[dict(identifier="R", name="Race", starts_at=deadline)],
    )
    calendar = CalendarService(db_session)
    row = calendar.save(CalendarWeekendInput(**data), "editor")
    fantasy = FantasyService(db_session, now=base)
    context = fantasy._load_race_context(race.id)
    if snapshot_exists:
        fantasy._questions_for_context(context)
        db_session.commit()
    clock[0] = base + timedelta(days=2)
    if suspend:
        data["status"] = "POSTPONED"
        row = calendar.save(
            CalendarWeekendInput(**data), "editor", row["id"], row["version"]
        )
    data["status"] = "SCHEDULED"
    data["sessions"][0]["starts_at"] = base + timedelta(days=4)
    calendar.save(
        CalendarWeekendInput(**data), "editor", row["id"], row["version"]
    )
    fantasy._fixed_now = clock[0]
    questions = fantasy._questions_for_context(context)
    assert questions
    assert all(fantasy._as_utc(q.locks_at) == deadline for q in questions)
    assert all(fantasy._question_is_locked(q) for q in questions)


def test_timely_reschedule_updates_stale_snapshot_after_old_deadline(
    db_session, monkeypatch
):
    base = datetime(2026, 1, 1, tzinfo=UTC)
    clock = [base]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    monkeypatch.setattr("app.services.calendar_service.datetime", Clock)
    meeting = Meeting(source="TEST", year=2026, name="Test")
    db_session.add(meeting)
    db_session.flush()
    race = RaceSession(
        meeting_id=meeting.id,
        name="Race",
        session_identifier="R",
        session_type="Race",
        started_at=base + timedelta(days=1),
    )
    db_session.add(race)
    db_session.commit()
    data = dict(
        year=2026,
        round_number=1,
        event_name="Test",
        meeting_id=meeting.id,
        source_url="https://example.com/schedule",
        source_checked_at=base,
        change_reason="Verified",
        sessions=[
            dict(
                identifier="R", name="Race", starts_at=base + timedelta(days=1)
            )
        ],
    )
    calendar = CalendarService(db_session)
    row = calendar.save(CalendarWeekendInput(**data), "editor")
    fantasy = FantasyService(db_session, now=base)
    context = fantasy._load_race_context(race.id)
    fantasy._questions_for_context(context)
    db_session.commit()
    clock[0] = base + timedelta(hours=1)
    new_deadline = base + timedelta(days=3)
    data["sessions"][0]["starts_at"] = new_deadline
    calendar.save(
        CalendarWeekendInput(**data), "editor", row["id"], row["version"]
    )
    fantasy._fixed_now = base + timedelta(days=2)
    questions = fantasy._questions_for_context(context)
    assert questions
    assert all(fantasy._as_utc(q.locks_at) == new_deadline for q in questions)
    assert all(not fantasy._question_is_locked(q) for q in questions)
