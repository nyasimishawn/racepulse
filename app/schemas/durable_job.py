from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.durable_job import DurableJobStatus, DurableJobType


class DurableJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_type: DurableJobType
    target_id: UUID | None
    status: DurableJobStatus
    progress_percentage: int
    attempt_count: int
    max_attempts: int
    next_retry_at: datetime | None
    cancel_requested_at: datetime | None
    cancelled_at: datetime | None
    failure_reason: str | None
    result: dict[str, object]
    queued_at: datetime
    started_at: datetime | None = None
    leased_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DurableJobCancelResponse(BaseModel):
    job: DurableJobResponse
    cancellation_effective: bool
