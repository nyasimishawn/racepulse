from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.durable_job import (
    DurableJob,
    DurableJobStatus,
    DurableJobType,
)
from app.models.import_job import DataSource, ImportJob, ImportJobStatus
from app.services.durable_job_service import (
    DurableJobService,
    JobStateError,
)
from app.services.durable_job_target_service import (
    mark_cancelled_import_target,
)


class RecordingPublisher:
    def __init__(self) -> None:
        self.job_ids: list[UUID] = []

    def publish(self, job_id: UUID) -> str:
        self.job_ids.append(job_id)
        return "1-0"


def test_enqueue_is_idempotent_and_keeps_a_single_durable_job(
    db_session: Session,
) -> None:
    publisher = RecordingPublisher()
    service = DurableJobService(
        db_session,
        publisher=publisher,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    target_id = uuid4()

    first = service.enqueue(
        job_type=DurableJobType.SESSION_IMPORT,
        target_id=target_id,
        idempotency_key=" import:2026:test-race ",
        payload={"source": "FASTF1"},
    )
    second = service.enqueue(
        job_type=DurableJobType.SESSION_IMPORT,
        target_id=target_id,
        idempotency_key="import:2026:test-race",
        payload={"source": "ignored-on-retry"},
    )

    persisted = db_session.scalars(select(DurableJob)).all()

    assert second.id == first.id
    assert len(persisted) == 1
    assert persisted[0].idempotency_key == "import:2026:test-race"
    assert persisted[0].payload == {"source": "FASTF1"}
    assert publisher.job_ids == [first.id, first.id]


def test_claim_and_retry_transitions_respect_backoff_and_attempt_limit(
    db_session: Session,
) -> None:
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    publisher = RecordingPublisher()
    service = DurableJobService(db_session, publisher=publisher, now=now)
    job = service.enqueue(
        job_type=DurableJobType.SESSION_TELEMETRY_IMPORT,
        target_id=uuid4(),
        idempotency_key="telemetry:retry",
        max_attempts=2,
    )

    first_claim = service.claim(job.id, lease_owner="worker-a")
    assert first_claim is not None
    assert first_claim.status == DurableJobStatus.RUNNING
    assert first_claim.attempt_count == 1
    assert first_claim.lease_owner == "worker-a"

    waiting = service.fail(job.id, "temporary provider failure")
    retry_at = now + timedelta(seconds=10)

    assert waiting.status == DurableJobStatus.RETRY_WAIT
    assert DurableJobService._as_utc(waiting.next_retry_at) == retry_at
    assert service.claim(job.id, lease_owner="worker-b") is None

    retry_service = DurableJobService(
        db_session,
        publisher=publisher,
        now=retry_at,
    )
    second_claim = retry_service.claim(job.id, lease_owner="worker-b")
    assert second_claim is not None
    assert second_claim.status == DurableJobStatus.RUNNING
    assert second_claim.attempt_count == 2

    failed = retry_service.fail(job.id, "final provider failure")

    assert failed.status == DurableJobStatus.FAILED
    assert failed.completed_at is not None
    assert failed.next_retry_at is None


def test_cancellation_transitions_cover_queued_and_running_jobs(
    db_session: Session,
) -> None:
    now = datetime(2026, 1, 2, tzinfo=UTC)
    service = DurableJobService(db_session, now=now)
    queued = service.enqueue(
        job_type=DurableJobType.SESSION_MAP_IMPORT,
        target_id=uuid4(),
        idempotency_key="map:queued-cancel",
    )

    cancelled, effective = service.request_cancel(queued.id)

    assert effective is True
    assert cancelled.status == DurableJobStatus.CANCELLED
    assert DurableJobService._as_utc(cancelled.cancelled_at) == now
    assert service.claim(queued.id, lease_owner="worker-a") is None

    running = service.enqueue(
        job_type=DurableJobType.RACE_CONTEXT_IMPORT,
        target_id=uuid4(),
        idempotency_key="context:running-cancel",
    )
    claimed = service.claim(running.id, lease_owner="worker-a")
    requested, effective = service.request_cancel(running.id)
    assert claimed is not None
    assert effective is True
    assert requested.status == DurableJobStatus.CANCEL_REQUESTED

    completed = service.complete(running.id, result={"ignored": True})

    assert completed.status == DurableJobStatus.CANCELLED
    assert completed.result == {}


def test_expired_leases_retry_cancel_or_fail_deterministically(
    db_session: Session,
) -> None:
    now = datetime(2026, 1, 3, 12, tzinfo=UTC)
    service = DurableJobService(db_session, now=now)
    retrying = service.enqueue(
        job_type=DurableJobType.SESSION_IMPORT,
        target_id=uuid4(),
        idempotency_key="expired:retry",
        max_attempts=2,
    )
    exhausted = service.enqueue(
        job_type=DurableJobType.SESSION_IMPORT,
        target_id=uuid4(),
        idempotency_key="expired:fail",
        max_attempts=1,
    )
    cancelling = service.enqueue(
        job_type=DurableJobType.FANTASY_SCORE,
        target_id=uuid4(),
        idempotency_key="expired:cancel",
    )

    assert service.claim(retrying.id, lease_owner="worker-a") is not None
    assert service.claim(exhausted.id, lease_owner="worker-a") is not None
    assert service.claim(cancelling.id, lease_owner="worker-a") is not None
    requested, effective = service.request_cancel(cancelling.id)
    assert effective is True
    assert requested.status == DurableJobStatus.CANCEL_REQUESTED

    expired_at = now - timedelta(seconds=61)
    for job in (retrying, exhausted, cancelling):
        db_session.refresh(job)
        job.leased_at = expired_at
    db_session.commit()

    recovered = service.recover_expired_leases(lease_seconds=60)
    for job in (retrying, exhausted, cancelling):
        db_session.refresh(job)

    assert recovered == 3
    assert retrying.status == DurableJobStatus.RETRY_WAIT
    assert DurableJobService._as_utc(retrying.next_retry_at) == now
    assert retrying.lease_owner is None
    assert exhausted.status == DurableJobStatus.FAILED
    assert exhausted.failure_reason == "Worker lease expired after final attempt."
    assert cancelling.status == DurableJobStatus.CANCELLED
    assert DurableJobService._as_utc(cancelling.cancelled_at) == now


def test_terminal_transitions_require_a_claim_and_progress_is_monotonic(
    db_session: Session,
) -> None:
    service = DurableJobService(db_session)
    job = service.enqueue(
        job_type=DurableJobType.SESSION_IMPORT,
        target_id=uuid4(),
        idempotency_key="safe-transition",
    )

    with pytest.raises(JobStateError):
        service.complete(job.id)

    assert service.claim(job.id, lease_owner="worker-a") is not None
    with pytest.raises(JobStateError):
        service.update_progress(
            job.id,
            10,
            lease_owner="worker-b",
        )
    assert service.update_progress(job.id, 45).progress_percentage == 45
    assert service.update_progress(job.id, 10).progress_percentage == 45
    assert service.update_progress(job.id, 500).progress_percentage == 99


def test_queued_cancellation_marks_only_its_linked_import_target(
    db_session: Session,
) -> None:
    target = ImportJob(
        source=DataSource.FASTF1,
        year=2026,
        event_name="Queue Test Grand Prix",
        session_type="Race",
        status=ImportJobStatus.PENDING,
    )
    db_session.add(target)
    db_session.commit()

    service = DurableJobService(db_session)
    job = service.enqueue(
        job_type=DurableJobType.SESSION_IMPORT,
        target_id=target.id,
        idempotency_key="target-cancel",
    )
    target.durable_job_id = job.id
    db_session.commit()

    cancelled, effective = service.request_cancel(job.id)

    assert effective is True
    assert mark_cancelled_import_target(db_session, cancelled) is True
    db_session.refresh(target)
    assert target.status == ImportJobStatus.CANCELLED
    assert target.error_message == "Cancelled by an editor."
