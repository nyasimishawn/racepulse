from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.models.calendar import CalendarRevision, CalendarWeekend
from app.schemas.calendar import CalendarWeekendInput
from app.services.calendar_service import CalendarService
from app.services.calendar_sync_service import sync_calendar


@pytest.fixture(autouse=True)
def require_postgresql(request):
    if not request.config.getoption("--postgresql"):
        pytest.skip("Requires --postgresql and a temporary migrated database")


def test_calendar_concurrent_edits_have_one_winner(db_session):
    data = CalendarWeekendInput(
        year=2026,
        round_number=1,
        event_name="Test",
        source_url="https://example.com/schedule",
        source_checked_at=datetime.now(UTC),
        change_reason="Initial",
        sessions=[
            dict(
                identifier="R",
                name="Race",
                starts_at=(datetime.now(UTC) + timedelta(days=1)),
            )
        ],
    )
    row = CalendarService(db_session).save(data, "editor")
    barrier = Barrier(2)

    def edit(name):
        with Session(db_session.bind) as session:
            cached = session.get(CalendarWeekend, row["id"])
            assert cached.version == 1
            barrier.wait(timeout=10)
            try:
                CalendarService(session).save(
                    data.model_copy(update={"event_name": name}),
                    name,
                    row["id"],
                    1,
                )
                return "saved"
            except StaleDataError:
                session.rollback()
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(edit, ["Editor A", "Editor B"]))
    assert sorted(results) == ["conflict", "saved"]
    db_session.expire_all()
    revisions = db_session.scalars(
        select(CalendarRevision).order_by(CalendarRevision.version)
    ).all()
    assert [revision.version for revision in revisions] == [1, 2]


def test_sync_lock_blocks_overlap_and_is_released_on_failure(db_session):
    with db_session.bind.connect() as lock:
        lock.execute(text("SELECT pg_advisory_lock(822026)"))
        try:
            assert sync_calendar(db_session, 2026) == {"status": "BUSY"}
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(822026)"))
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(502))
    ) as client:
        assert sync_calendar(db_session, 2026, client)["status"] == "FAILED"
    with db_session.bind.connect() as lock:
        acquired = lock.scalar(text("SELECT pg_try_advisory_lock(822026)"))
        try:
            assert acquired is True
        finally:
            if acquired:
                lock.execute(text("SELECT pg_advisory_unlock(822026)"))


def test_real_redis_delivery_is_idempotent_and_recovers_worker_lease(
    db_session, monkeypatch
):
    from uuid import uuid4

    from redis import Redis
    from sqlalchemy.orm import sessionmaker

    from app.core.config import settings
    from app.models.durable_job import DurableJobStatus, DurableJobType
    from app.services.durable_job_service import DurableJobService
    from app.workers.job_queue import RedisStreamJobPublisher
    from app.workers.job_worker import DurableJobWorker

    stream = "racepulse:test:" + uuid4().hex
    monkeypatch.setattr(settings, "job_stream_key", stream)
    monkeypatch.setattr(settings, "job_consumer_group", "test-workers")
    monkeypatch.setattr(
        "app.workers.job_worker.SessionLocal",
        sessionmaker(bind=db_session.bind, autoflush=False),
    )
    executed = []

    def run_job(self, db, service, job):
        executed.append(job.id)
        return {"verified": True}

    monkeypatch.setattr(DurableJobWorker, "_run_job", run_job)
    with Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=3,
    ) as redis:
        try:
            assert redis.ping()
            publisher = RedisStreamJobPublisher(redis)
            publisher.ensure_consumer_group()
            service = DurableJobService(db_session, publisher=publisher)
            job = service.enqueue(
                job_type=DurableJobType.SESSION_IMPORT,
                target_id=uuid4(),
                idempotency_key="integration-import",
            )
            # Simulate a crash after claiming in PostgreSQL. Redis delivery
            # remains available; the next worker recovers the expired lease.
            old = datetime.now(UTC) - timedelta(
                seconds=settings.job_lease_seconds + 60
            )
            DurableJobService(db_session, now=old).claim(
                job.id, lease_owner="crashed-worker"
            )
            worker = DurableJobWorker(redis, worker_id="replacement-worker")
            worker.run_once()
            db_session.expire_all()
            recovered = service.get(job.id)
            assert recovered.status == DurableJobStatus.COMPLETED
            assert recovered.attempt_count == 2
            publisher.publish(job.id)
            worker.run_once()
            assert executed == [job.id]
            assert redis.xpending(stream, "test-workers")["pending"] == 0
        finally:
            redis.delete(stream)
