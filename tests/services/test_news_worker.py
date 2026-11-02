from datetime import UTC, datetime, timedelta

from app.models.calendar import CalendarWeekend
from app.models.news import NewsSourceState
from app.providers.news_provider import PublisherError, make_item
from app.workers.news_worker import acquire_lease, interval_seconds, sync_once


def test_only_one_collector_and_expired_owner_can_be_replaced(db_session):
    now = datetime.now(UTC)
    assert acquire_lease(db_session, "worker-a", now)
    assert not acquire_lease(db_session, "worker-b", now)
    assert acquire_lease(db_session, "worker-a", now + timedelta(seconds=1))
    assert acquire_lease(db_session, "worker-b", now + timedelta(minutes=6))


def test_partial_feed_failure_preserves_other_source_success(db_session):
    class Provider:
        def fetch(self, source_id):
            if source_id == "autosport_f1":
                raise PublisherError("Publisher unavailable (403).")
            return [
                make_item(
                    source_id,
                    "Mercedes news",
                    "https://www.motorsport.com/f1/news/mercedes/1/",
                    "2026-01-01T09:00:00Z",
                )
            ]

    assert sync_once(db_session, Provider()) == 1
    assert (
        db_session.get(NewsSourceState, "motorsport_f1").last_success_at
        is not None
    )
    assert db_session.get(NewsSourceState, "autosport_f1").status == "ERROR"
    assert (
        db_session.get(NewsSourceState, "autosport_f1").last_success_at is None
    )


def test_cancelled_sessions_do_not_turn_on_weekend_polling(db_session):
    now = datetime.now(UTC)
    weekend = CalendarWeekend(
        year=now.year,
        event_name="Test GP",
        updated_at=now,
        schedule={
            "sessions": [{"starts_at": now.isoformat(), "status": "CANCELLED"}]
        },
    )
    db_session.add(weekend)
    db_session.commit()
    assert interval_seconds(db_session, now) == 600
    weekend.schedule = {
        "sessions": [{"starts_at": now.isoformat(), "status": "IN_PROGRESS"}]
    }
    db_session.commit()
    assert interval_seconds(db_session, now) == 120


def test_publisher_cooldown_survives_worker_restarts(db_session):
    retry = datetime.now(UTC) + timedelta(hours=1)
    db_session.add(
        NewsSourceState(
            source_id="motorsport_f1",
            publisher="Motorsport.com",
            status="ERROR",
            message="Rate limited",
            archive={"feed_retry_after": retry.isoformat()},
        )
    )
    db_session.commit()
    called = []

    class Provider:
        def fetch(self, source_id):
            called.append(source_id)
            return []

    sync_once(db_session, Provider())
    assert called == ["autosport_f1"]
    state = db_session.get(NewsSourceState, "motorsport_f1")
    assert state.status == "ERROR"
    assert state.last_checked_at is None
