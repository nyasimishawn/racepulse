from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.import_job import ImportJobCreate, ImportJobResponse
from app.services.import_job_service import ImportJobService
from app.services.session_import_service import (
    ImportJobNotFoundError,
    ImportJobStateError,
    SessionImportError,
    SessionImportService,
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
    db: Session = Depends(get_db),
) -> ImportJobResponse:
    return ImportJobService.create(db, payload)


@router.get(
    "/{job_id}",
    response_model=ImportJobResponse,
    summary="Get import-job status",
)
def get_import_job(
    job_id: UUID,
    db: Session = Depends(get_db),
) -> ImportJobResponse:
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
    db: Session = Depends(get_db),
) -> ImportJobResponse:
    try:
        return SessionImportService(db).run(job_id)

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