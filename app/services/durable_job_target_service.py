from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.durable_job import (
    DurableJob,
    DurableJobStatus,
    DurableJobType,
)
from app.models.import_job import ImportJob, ImportJobStatus
from app.models.session_map_import import (
    SessionMapImport,
    SessionMapImportStatus,
)
from app.models.session_telemetry_import import (
    SessionTelemetryImport,
    SessionTelemetryImportStatus,
)


def mark_cancelled_import_target(
    db: Session,
    durable_job: DurableJob,
) -> bool:
    """Mirror an effective durable cancellation onto its import target.

    The durable-job link is checked before every mutation so an old recovered
    job cannot overwrite a newer requeue of the same import record.
    """
    if durable_job.target_id is None:
        return False

    cancelled_at = datetime.now(UTC)

    if durable_job.job_type == DurableJobType.SESSION_IMPORT:
        target = db.get(ImportJob, durable_job.target_id)
        if (
            target is None
            or target.durable_job_id != durable_job.id
            or target.status not in {
                ImportJobStatus.PENDING,
                ImportJobStatus.RUNNING,
            }
        ):
            return False
        target.status = ImportJobStatus.CANCELLED
        target.error_message = "Cancelled by an editor."
        target.completed_at = cancelled_at

    elif durable_job.job_type == DurableJobType.SESSION_TELEMETRY_IMPORT:
        target = db.get(SessionTelemetryImport, durable_job.target_id)
        if (
            target is None
            or target.durable_job_id != durable_job.id
            or target.status not in {
                SessionTelemetryImportStatus.PENDING,
                SessionTelemetryImportStatus.RUNNING,
            }
        ):
            return False
        target.status = SessionTelemetryImportStatus.CANCELLED
        target.current_driver_number = None
        target.error_message = "Cancelled by an editor."
        target.completed_at = cancelled_at

    elif durable_job.job_type == DurableJobType.SESSION_MAP_IMPORT:
        target = db.get(SessionMapImport, durable_job.target_id)
        if (
            target is None
            or target.durable_job_id != durable_job.id
            or target.status not in {
                SessionMapImportStatus.PENDING,
                SessionMapImportStatus.RUNNING,
            }
        ):
            return False
        target.status = SessionMapImportStatus.CANCELLED
        target.current_driver_number = None
        target.error_message = "Cancelled by an editor."
        target.completed_at = cancelled_at

    else:
        return False

    db.commit()
    return True


def synchronize_cancelled_import_targets(db: Session) -> int:
    """Repair target state after queued or lease-recovery cancellations."""
    jobs = db.scalars(
        select(DurableJob).where(
            DurableJob.status == DurableJobStatus.CANCELLED,
            DurableJob.job_type.in_(
                [
                    DurableJobType.SESSION_IMPORT,
                    DurableJobType.SESSION_TELEMETRY_IMPORT,
                    DurableJobType.SESSION_MAP_IMPORT,
                ]
            ),
        )
    ).all()
    return sum(mark_cancelled_import_target(db, job) for job in jobs)
