from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models.alert import Alert, AlertStatus, AlertType
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.user_profile import UserProfile
from app.schemas.calendar import CalendarWeekendInput
from app.services.alert_service import AlertService
from app.services.calendar_service import CalendarService
from app.models.calendar import CalendarWeekend


def make_weekend(db, now, *, linked=False):
    meeting = None
    if linked:
        meeting = Meeting(
            source="TEST", year=now.year, name="Example Grand Prix"
        )
        db.add(meeting)
        db.flush()
        db.add(
            RaceSession(
                meeting_id=meeting.id,
                name="Race",
                session_identifier="R",
                session_type="Race",
                started_at=now + timedelta(days=2),
            )
        )
        db.commit()
    data = dict(
        year=now.year,
        round_number=1,
        event_name="Example Grand Prix",
        source_url="https://example.com/calendar",
        source_checked_at=now,
        change_reason="Verified",
        meeting_id=meeting.id if meeting else None,
        sessions=[
            dict(
                identifier="R", name="Race", starts_at=now + timedelta(days=2)
            )
        ],
    )
    overview = CalendarService(db).save(CalendarWeekendInput(**data), "editor")
    profile = UserProfile(keycloak_subject="fan-one")
    db.add(profile)
    db.commit()
    return db.get(CalendarWeekend, overview["id"]), profile, data


def alerts(db, kind=None):
    rows = db.scalars(select(Alert)).all()
    return [row for row in rows if kind is None or row.alert_type == kind]


def test_reschedule_replaces_pending_reminders_and_cancellation(db_session):
    now = datetime.now(UTC)
    weekend, profile, data = make_weekend(db_session, now, linked=True)
    AlertService(db_session, now=now).set_preference(
        profile.id,
        weekend,
        session_soon=True,
        fantasy_deadline=True,
        schedule_change=True,
    )
    reminders = alerts(db_session)
    assert {a.alert_type for a in reminders} == {
        AlertType.SESSION_SOON,
        AlertType.FANTASY_DEADLINE,
    }
    data["sessions"][0]["starts_at"] = now + timedelta(days=3)
    data["change_reason"] = "Race moved one day"
    CalendarService(db_session).save(
        CalendarWeekendInput(**data), "editor", weekend.id, weekend.version
    )
    assert (
        len(
            [
                a
                for a in alerts(db_session)
                if a.status == AlertStatus.CANCELLED
            ]
        )
        == 2
    )
    assert (
        len([a for a in alerts(db_session) if a.status == AlertStatus.PENDING])
        == 3
    )
    assert len(alerts(db_session, AlertType.SCHEDULE_CHANGE)) == 1
    # A source check with identical times/status does not generate an alert.
    data["source_checked_at"] = datetime.now(UTC)
    CalendarService(db_session).save(
        CalendarWeekendInput(**data), "editor", weekend.id, weekend.version
    )
    assert len(alerts(db_session)) == 5
    data["sessions"][0]["status"] = "CANCELLED"
    data["change_reason"] = "Race cancelled"
    CalendarService(db_session).save(
        CalendarWeekendInput(**data), "editor", weekend.id, weekend.version
    )
    assert all(
        a.status == AlertStatus.CANCELLED
        for a in alerts(db_session)
        if a.alert_type != AlertType.SCHEDULE_CHANGE
    )
    assert len(alerts(db_session, AlertType.SCHEDULE_CHANGE)) == 2


def test_worker_recovery_retries_failed_push_and_respects_disabled_preference(
    db_session,
):
    now = datetime.now(UTC)
    weekend, profile, _ = make_weekend(db_session, now)
    AlertService(db_session, now=now).set_preference(
        profile.id,
        weekend,
        session_soon=True,
        fantasy_deadline=False,
        schedule_change=False,
    )
    due = now + timedelta(days=2, minutes=-29)

    class FailingPush:
        def send(self, alert):
            raise RuntimeError("temporary failure")

    assert (
        AlertService(db_session, now=due, push=FailingPush()).deliver_due()
        == 0
    )
    assert alerts(db_session)[0].status == AlertStatus.PENDING

    class RecordingPush:
        ids = []

        def send(self, alert):
            self.ids.append(alert.id)

    push = RecordingPush()
    assert AlertService(db_session, now=due, push=push).deliver_due() == 1
    assert AlertService(db_session, now=due, push=push).deliver_due() == 0
    assert len(push.ids) == 1
    AlertService(db_session, now=due).set_preference(
        profile.id,
        weekend,
        session_soon=False,
        fantasy_deadline=False,
        schedule_change=False,
    )
    assert (
        len(
            [
                a
                for a in alerts(db_session)
                if a.status == AlertStatus.DELIVERED
            ]
        )
        == 1
    )


def test_existing_worker_recovery_delivers_database_due_alerts(
    db_session, monkeypatch
):
    from sqlalchemy.orm import sessionmaker
    from app.workers import job_worker

    now = datetime.now(UTC)
    weekend, profile, _ = make_weekend(db_session, now)
    AlertService(db_session, now=now).set_preference(
        profile.id,
        weekend,
        session_soon=True,
        fantasy_deadline=False,
        schedule_change=False,
    )
    alert = alerts(db_session)[0]
    alert.scheduled_for = now - timedelta(minutes=1)
    db_session.commit()
    factory = sessionmaker(bind=db_session.bind)
    monkeypatch.setattr(job_worker, "SessionLocal", factory)
    worker = job_worker.DurableJobWorker(object())
    worker._recover_if_due()
    db_session.expire_all()
    assert db_session.get(Alert, alert.id).status == AlertStatus.DELIVERED
    worker._last_recovery_at = None
    worker._recover_if_due()
    assert len(alerts(db_session)) == 1


def test_linking_imported_fantasy_meeting_adds_deadline_without_schedule_change(
    db_session,
):
    now = datetime.now(UTC)
    weekend, profile, data = make_weekend(db_session, now)
    AlertService(db_session, now=now).set_preference(
        profile.id,
        weekend,
        session_soon=False,
        fantasy_deadline=True,
        schedule_change=True,
    )
    assert alerts(db_session) == []
    meeting = Meeting(source="TEST", year=now.year, name="Example Grand Prix")
    db_session.add(meeting)
    db_session.flush()
    db_session.add(
        RaceSession(
            meeting_id=meeting.id,
            name="Race",
            session_identifier="R",
            session_type="Race",
            started_at=now + timedelta(days=2),
        )
    )
    db_session.commit()
    data["meeting_id"] = meeting.id
    CalendarService(db_session).save(
        CalendarWeekendInput(**data), "editor", weekend.id, weekend.version
    )
    assert len(alerts(db_session, AlertType.FANTASY_DEADLINE)) == 1
    assert alerts(db_session, AlertType.SCHEDULE_CHANGE) == []


def test_unfollow_cancels_pending_alerts(db_session):
    now = datetime.now(UTC)
    weekend, profile, _ = make_weekend(db_session, now)
    service = AlertService(db_session, now=now)
    service.set_preference(
        profile.id,
        weekend,
        session_soon=True,
        fantasy_deadline=False,
        schedule_change=False,
    )
    service.remove_preference(profile.id, weekend.id)
    assert alerts(db_session)[0].status == AlertStatus.CANCELLED
    assert (
        AlertService(db_session, now=now + timedelta(days=2)).deliver_due()
        == 0
    )


def test_worker_finds_fantasy_deadline_after_late_session_import(
    db_session, monkeypatch
):
    from sqlalchemy.orm import sessionmaker
    from app.workers import job_worker

    now = datetime.now(UTC)
    weekend, profile, data = make_weekend(db_session, now)
    AlertService(db_session, now=now).set_preference(
        profile.id,
        weekend,
        session_soon=False,
        fantasy_deadline=True,
        schedule_change=False,
    )
    meeting = Meeting(source="TEST", year=now.year, name="Example Grand Prix")
    db_session.add(meeting)
    db_session.commit()
    data["meeting_id"] = meeting.id
    CalendarService(db_session).save(
        CalendarWeekendInput(**data), "editor", weekend.id, weekend.version
    )
    assert alerts(db_session) == []
    db_session.add(
        RaceSession(
            meeting_id=meeting.id,
            name="Race",
            session_identifier="R",
            session_type="Race",
            started_at=now + timedelta(days=2),
        )
    )
    db_session.commit()
    monkeypatch.setattr(
        job_worker, "SessionLocal", sessionmaker(bind=db_session.bind)
    )
    worker = job_worker.DurableJobWorker(object())
    worker._recover_if_due()
    db_session.expire_all()
    assert len(alerts(db_session, AlertType.FANTASY_DEADLINE)) == 1
    worker._last_recovery_at = None
    worker._recover_if_due()
    db_session.expire_all()
    assert len(alerts(db_session, AlertType.FANTASY_DEADLINE)) == 1


def test_fantasy_reminders_only_target_sessions_with_prediction_questions(
    db_session,
):
    now = datetime.now(UTC)
    weekend, profile, data = make_weekend(db_session, now, linked=True)
    data["sessions"].append(
        dict(
            identifier="SQ",
            name="Sprint Qualifying",
            starts_at=now + timedelta(days=1),
        )
    )
    db_session.add(
        RaceSession(
            meeting_id=weekend.meeting_id,
            name="Sprint Qualifying",
            session_identifier="SQ",
            session_type="Sprint Qualifying",
            started_at=now + timedelta(days=1),
        )
    )
    db_session.commit()
    CalendarService(db_session).save(
        CalendarWeekendInput(**data), "editor", weekend.id, weekend.version
    )
    AlertService(db_session, now=now).set_preference(
        profile.id,
        weekend,
        session_soon=False,
        fantasy_deadline=True,
        schedule_change=False,
    )
    assert [
        a.session_identifier
        for a in alerts(db_session, AlertType.FANTASY_DEADLINE)
    ] == ["R"]
