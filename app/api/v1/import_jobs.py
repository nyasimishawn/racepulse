from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.rate_limit import expensive_request_rate_limit
from app.core.security import AuthenticatedUser, require_roles
from app.db.database import get_db
from app.models.durable_job import DurableJobType
from app.models.import_job import ImportJob
from app.schemas.import_job import ImportJobCreate, ImportJobResponse
from app.services.import_job_service import ImportJobService
from app.services.job_dispatch_service import enqueue_job
from app.services.session_import_service import (
    ImportJobNotFoundError,
    ImportJobStateError,
    SessionImportError,
)

router = APIRouter(prefix="/import-jobs", tags=["Import Jobs"])


@router.post(
    "",
    response_model=ImportJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue an F1 session import",
)
def create_import_job(
    payload: ImportJobCreate,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        max_length=255,
    ),
    _: None = Depends(expensive_request_rate_limit),
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> ImportJobResponse:
    del current_user
    job = ImportJobService.create(
        db,
        payload,
        idempotency_key=idempotency_key,
    )
    return _queue_import(db, job, idempotency_key)


@router.get(
    "/{job_id}",
    response_model=ImportJobResponse,
    summary="Get import-job status",
)
def get_import_job(
    job_id: UUID,
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> ImportJobResponse:
    del current_user
    job = ImportJobService.get_by_id(db, job_id)

    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Import job not found",
        )

    return job


@router.post(
    "/{job_id}/run",
    response_model=ImportJobResponse,
    summary="Run a queued FastF1 import",
)
def run_import_job(
    job_id: UUID,
    _: None = Depends(expensive_request_rate_limit),
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> ImportJobResponse:
    del current_user
    try:
        job = ImportJobService.get_by_id(db, job_id)
        if job is None:
            raise ImportJobNotFoundError("Import job not found.")
        return _queue_import(db, job, job.idempotency_key)

    except ImportJobNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except ImportJobStateError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error

    except SessionImportError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        ) from error


def _queue_import(
    db: Session,
    job: ImportJob,
    idempotency_key: str | None,
) -> ImportJob:
    durable_job = enqueue_job(
        db,
        job_type=DurableJobType.SESSION_IMPORT,
        target_id=job.id,
        idempotency_key=(
            f"session-import:{idempotency_key}"
            if idempotency_key
            else None
        ),
        payload={"import_job_id": str(job.id)},
    )
    if job.durable_job_id != durable_job.id:
        job.durable_job_id = durable_job.id
        db.commit()
        db.refresh(job)
    return job
