from uuid import UUID

from sqlalchemy.orm import Session

from app.models.import_job import ImportJob, ImportJobStatus
from app.schemas.import_job import ImportJobCreate


class ImportJobService:
    @staticmethod
    def create(db: Session, payload: ImportJobCreate) -> ImportJob:
        job = ImportJob(
            source=payload.source,
            year=payload.year,
            event_name=payload.event_name.strip(),
            session_type=payload.session_type.strip(),
            status=ImportJobStatus.PENDING,
            progress_percentage=0,
        )

        db.add(job)
        db.commit()
        db.refresh(job)

        return job

    @staticmethod
    def get_by_id(db: Session, job_id: UUID) -> ImportJob | None:
        return db.get(ImportJob, job_id)