from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.core.redis import create_sync_redis_client
from app.models.durable_job import DurableJob, DurableJobType
from app.services.durable_job_service import DurableJobService
from app.workers.job_queue import RedisStreamJobPublisher


def enqueue_job(
    db: Session,
    *,
    job_type: DurableJobType,
    target_id: UUID,
    idempotency_key: str | None = None,
    payload: dict[str, object] | None = None,
) -> DurableJob:
    """Persist then attempt dispatch; worker recovery covers Redis outages."""
    redis = create_sync_redis_client()
    try:
        return DurableJobService(
            db,
            publisher=RedisStreamJobPublisher(redis),
        ).enqueue(
            job_type=job_type,
            target_id=target_id,
            idempotency_key=(
                idempotency_key
                or f"{job_type.value.casefold()}:{target_id}"
            ),
            payload=payload,
        )
    finally:
        redis.close()
