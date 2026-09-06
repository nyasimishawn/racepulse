from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from redis.exceptions import RedisError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.durable_job import (
    DurableJob,
    DurableJobStatus,
    DurableJobType,
)


logger = logging.getLogger(__name__)


class JobPublisher(Protocol):
    def publish(self, job_id: UUID) -> str:
        """Put a durable-job identifier onto the shared Redis stream."""


class JobNotFoundError(LookupError):
    pass


class JobCancellationRequested(RuntimeError):
    pass


class JobStateError(RuntimeError):
    pass


class DurableJobService:
    """Safe state transitions for the application's sole job architecture."""

    def __init__(
        self,
        db: Session,
        *,
        publisher: JobPublisher | None = None,
        now: datetime | None = None,
    ) -> None:
        self.db = db
        self.publisher = publisher
        self._fixed_now = now

    def enqueue(
        self,
        *,
        job_type: DurableJobType,
        target_id: UUID | None,
        idempotency_key: str,
        payload: dict[str, object] | None = None,
        max_attempts: int | None = None,
    ) -> DurableJob:
        normalized_key = self._idempotency_key(idempotency_key)
        existing = self.db.scalar(
            select(DurableJob).where(
                DurableJob.job_type == job_type,
                DurableJob.idempotency_key == normalized_key,
            )
        )
        if existing is not None:
            self.dispatch(existing)
            return existing

        job = DurableJob(
            job_type=job_type,
            target_id=target_id,
            idempotency_key=normalized_key,
            payload=dict(payload or {}),
            max_attempts=max(1, max_attempts or settings.job_max_attempts),
            status=DurableJobStatus.QUEUED,
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        self.dispatch(job)
        return job

    def get(self, job_id: UUID) -> DurableJob:
        job = self.db.get(DurableJob, job_id)
        if job is None:
            raise JobNotFoundError("Durable job was not found.")
        return job

    def dispatch(self, job: DurableJob) -> bool:
        """Best-effort transport; PostgreSQL recovery prevents job loss."""
        if self.publisher is None or job.status not in {
            DurableJobStatus.QUEUED,
            DurableJobStatus.RETRY_WAIT,
        }:
            return False

        if (
            job.next_retry_at is not None
            and self._as_utc(job.next_retry_at) > self._now()
        ):
            return False

        try:
            self.publisher.publish(job.id)
        except RedisError:
            logger.warning(
                "Durable job transport unavailable for job=%s.",
                job.id,
            )
            return False

        job.last_dispatched_at = self._now()
        self.db.commit()
        self.db.refresh(job)
        return True

    def claim(
        self,
        job_id: UUID,
        *,
        lease_owner: str,
    ) -> DurableJob | None:
        statement = (
            select(DurableJob)
            .where(DurableJob.id == job_id)
            .with_for_update()
        )
        job = self.db.scalar(statement)
        if job is None:
            return None

        if job.status == DurableJobStatus.CANCEL_REQUESTED:
            self._mark_cancelled(job)
            return None

        if job.status not in {
            DurableJobStatus.QUEUED,
            DurableJobStatus.RETRY_WAIT,
        }:
            return None

        if (
            job.next_retry_at is not None
            and self._as_utc(job.next_retry_at) > self._now()
        ):
            return None

        job.status = DurableJobStatus.RUNNING
        job.attempt_count += 1
        job.progress_percentage = max(job.progress_percentage, 1)
        job.lease_owner = lease_owner[:160]
        job.leased_at = self._now()
        job.started_at = job.started_at or self._now()
        job.next_retry_at = None
        job.failure_reason = None
        self.db.commit()
        self.db.refresh(job)
        return job

    def complete(
        self,
        job_id: UUID,
        *,
        result: dict[str, object] | None = None,
        lease_owner: str | None = None,
    ) -> DurableJob:
        job = self.get(job_id)
        self._require_lease_owner(job, lease_owner)
        if job.status == DurableJobStatus.CANCEL_REQUESTED:
            self._mark_cancelled(job)
            return job
        if job.status != DurableJobStatus.RUNNING:
            raise JobStateError(
                "Only a running durable job can be completed."
            )

        job.status = DurableJobStatus.COMPLETED
        job.progress_percentage = 100
        job.result = dict(result or {})
        job.failure_reason = None
        job.completed_at = self._now()
        job.lease_owner = None
        job.leased_at = None
        self.db.commit()
        self.db.refresh(job)
        return job

    def fail(
        self,
        job_id: UUID,
        error: Exception | str,
        *,
        retryable: bool = True,
        lease_owner: str | None = None,
    ) -> DurableJob:
        job = self.get(job_id)
        self._require_lease_owner(job, lease_owner)
        if job.status == DurableJobStatus.CANCEL_REQUESTED:
            self._mark_cancelled(job)
            return job
        if job.status != DurableJobStatus.RUNNING:
            raise JobStateError(
                "Only a running durable job can be failed."
            )

        job.failure_reason = _safe_failure_reason(error)
        job.lease_owner = None
        job.leased_at = None

        if retryable and job.attempt_count < job.max_attempts:
            job.status = DurableJobStatus.RETRY_WAIT
            job.next_retry_at = self._now() + timedelta(
                seconds=min(300, 10 * (2 ** max(0, job.attempt_count - 1)))
            )
        else:
            job.status = DurableJobStatus.FAILED
            job.completed_at = self._now()

        self.db.commit()
        self.db.refresh(job)
        return job

    def update_progress(
        self,
        job_id: UUID,
        progress_percentage: int,
        *,
        lease_owner: str | None = None,
    ) -> DurableJob:
        """Persist monotonic progress for a running job without finishing it."""
        job = self.get(job_id)
        if job.status != DurableJobStatus.RUNNING:
            return job

        self._require_lease_owner(job, lease_owner)
        bounded = min(99, max(0, int(progress_percentage)))
        job.progress_percentage = max(job.progress_percentage, bounded)
        job.leased_at = self._now()
        self.db.commit()
        self.db.refresh(job)
        return job

    def renew_lease(
        self,
        job_id: UUID,
        *,
        lease_owner: str,
    ) -> DurableJob:
        job = self.db.scalar(
            select(DurableJob).where(DurableJob.id == job_id)
            .with_for_update().execution_options(populate_existing=True)
        )
        if job is None:
            raise JobNotFoundError("Durable job was not found.")
        self._require_lease_owner(job, lease_owner)
        if job.status == DurableJobStatus.RUNNING:
            job.leased_at = self._now()
            self.db.commit()
            self.db.refresh(job)
        return job

    def request_cancel(self, job_id: UUID) -> tuple[DurableJob, bool]:
        job = self.get(job_id)
        if job.status in {
            DurableJobStatus.COMPLETED,
            DurableJobStatus.FAILED,
            DurableJobStatus.CANCELLED,
        }:
            return job, False

        if job.status in {
            DurableJobStatus.QUEUED,
            DurableJobStatus.RETRY_WAIT,
        }:
            self._mark_cancelled(job)
            return job, True

        if job.status == DurableJobStatus.RUNNING:
            job.status = DurableJobStatus.CANCEL_REQUESTED
            job.cancel_requested_at = self._now()
            self.db.commit()
            self.db.refresh(job)
            return job, True

        return job, job.status == DurableJobStatus.CANCEL_REQUESTED

    def is_cancellation_requested(self, job_id: UUID) -> bool:
        self.db.expire_all()
        job = self.db.get(DurableJob, job_id)
        return job is not None and job.status in {
            DurableJobStatus.CANCEL_REQUESTED,
            DurableJobStatus.CANCELLED,
        }

    def raise_if_cancel_requested(self, job_id: UUID) -> None:
        if self.is_cancellation_requested(job_id):
            raise JobCancellationRequested("Durable job cancellation requested.")

    def recover_expired_leases(self, *, lease_seconds: int | None = None) -> int:
        threshold = self._now() - timedelta(
            seconds=max(1, lease_seconds or settings.job_lease_seconds)
        )
        jobs = self.db.scalars(
            select(DurableJob).where(
                DurableJob.status.in_(
                    [
                        DurableJobStatus.RUNNING,
                        DurableJobStatus.CANCEL_REQUESTED,
                    ]
                ),
                DurableJob.leased_at.is_not(None),
                DurableJob.leased_at < threshold,
            ).with_for_update(skip_locked=True)
        ).all()

        for job in jobs:
            if job.status == DurableJobStatus.CANCEL_REQUESTED:
                job.status = DurableJobStatus.CANCELLED
                job.cancelled_at = self._now()
                job.completed_at = self._now()
            elif job.attempt_count < job.max_attempts:
                job.status = DurableJobStatus.RETRY_WAIT
                job.next_retry_at = self._now()
                job.failure_reason = "Worker lease expired; queued for retry."
            else:
                job.status = DurableJobStatus.FAILED
                job.failure_reason = "Worker lease expired after final attempt."
                job.completed_at = self._now()
            job.lease_owner = None
            job.leased_at = None

        if jobs:
            self.db.commit()
        return len(jobs)

    def dispatch_ready(self, *, minimum_age_seconds: int = 20) -> int:
        now = self._now()
        stale_before = now - timedelta(seconds=max(0, minimum_age_seconds))
        jobs = self.db.scalars(
            select(DurableJob).where(
                DurableJob.status.in_(
                    [DurableJobStatus.QUEUED, DurableJobStatus.RETRY_WAIT]
                ),
                or_(
                    DurableJob.next_retry_at.is_(None),
                    DurableJob.next_retry_at <= now,
                ),
                or_(
                    DurableJob.last_dispatched_at.is_(None),
                    DurableJob.last_dispatched_at <= stale_before,
                ),
            )
        ).all()
        return sum(self.dispatch(job) for job in jobs)

    def _mark_cancelled(self, job: DurableJob) -> None:
        job.status = DurableJobStatus.CANCELLED
        job.cancel_requested_at = job.cancel_requested_at or self._now()
        job.cancelled_at = self._now()
        job.completed_at = self._now()
        job.lease_owner = None
        job.leased_at = None
        job.failure_reason = None
        self.db.commit()
        self.db.refresh(job)

    @staticmethod
    def _require_lease_owner(
        job: DurableJob,
        lease_owner: str | None,
    ) -> None:
        if lease_owner is not None and job.lease_owner != lease_owner:
            raise JobStateError(
                "The durable-job lease belongs to another worker."
            )

    @staticmethod
    def _idempotency_key(value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("A durable-job idempotency key is required.")
        return normalized[:255]

    def _now(self) -> datetime:
        return self._as_utc(self._fixed_now or datetime.now(UTC))

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def _safe_failure_reason(error: Exception | str) -> str:
    if isinstance(error, str):
        return "Background job execution failed."
    return f"{type(error).__name__}: Background job execution failed."
