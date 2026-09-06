from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.base import Base
from app.models.durable_job import DurableJobType
from app.services.durable_job_service import DurableJobService
from app.workers.job_worker import DurableJobWorker


def test_provider_work_keeps_its_lease_alive_without_progress_callbacks(
    monkeypatch,
):
    # Separate connections/threads share an isolated in-memory database.
    engine = create_engine(
        f"sqlite:///file:heartbeat-{uuid4()}?mode=memory&cache=shared&uri=true",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr("app.workers.job_worker.SessionLocal", factory)
    monkeypatch.setattr(settings, "job_lease_seconds", 1)
    with factory() as db:
        service = DurableJobService(
            db, now=datetime.now(UTC) - timedelta(minutes=10)
        )
        job = service.enqueue(
            job_type=DurableJobType.WEEKEND_IMPORT,
            target_id=uuid4(),
            idempotency_key="heartbeat-test",
        )
        job_id = job.id
        service.claim(job_id, lease_owner="test-worker")
    renewed = Event()
    renew = DurableJobService.renew_lease

    def signal_renewed(self, *args, **kwargs):
        result = renew(self, *args, **kwargs)
        renewed.set()
        return result

    monkeypatch.setattr(DurableJobService, "renew_lease", signal_renewed)
    worker = DurableJobWorker(SimpleNamespace(), worker_id="test-worker")
    try:
        with worker._lease_heartbeat(job_id):
            assert renewed.wait(3), (
                "Lease was not renewed during provider work."
            )
            with factory() as db:
                assert (
                    DurableJobService(db).recover_expired_leases(
                        lease_seconds=10
                    )
                    == 0
                )
    finally:
        engine.dispose()
