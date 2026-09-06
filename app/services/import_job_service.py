from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.import_job import ImportJob, ImportJobStatus
from app.schemas.import_job import ImportJobCreate


class ImportJobService:
    @staticmethod
    def create(
        db: Session,
        payload: ImportJobCreate,
        *,
        idempotency_key: str | None = None,
    ) -> ImportJob:
        normalized_key = (
            idempotency_key.strip()[:255]
            if idempotency_key and idempotency_key.strip()
            else None
        )
        if normalized_key is not None:
            existing = db.scalar(
                select(ImportJob).where(
                    ImportJob.idempotency_key == normalized_key
                )
            )
            if existing is not None:
                return existing

        job = ImportJob(
            source=payload.source,
            year=payload.year,
            event_name=payload.event_name.strip(),
            session_type=payload.session_type.strip(),
            status=ImportJobStatus.PENDING,
            progress_percentage=0,
            idempotency_key=normalized_key,
        )

        db.add(job)
        db.commit()
        db.refresh(job)

        return job

    @staticmethod
    def get_by_id(db: Session, job_id: UUID) -> ImportJob | None:
        return db.get(ImportJob, job_id)
