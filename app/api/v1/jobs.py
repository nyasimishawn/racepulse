from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import AuthenticatedUser, require_roles
from app.db.database import get_db
from app.schemas.durable_job import (
    DurableJobCancelResponse,
    DurableJobResponse,
)
from app.services.durable_job_service import (
    DurableJobService,
    JobNotFoundError,
)
from app.services.durable_job_target_service import (
    mark_cancelled_import_target,
)


router = APIRouter(prefix="/jobs", tags=["Durable Jobs"])


@router.get("/{job_id}", response_model=DurableJobResponse)
def get_job(
    job_id: UUID,
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> DurableJobResponse:
    del current_user
    try:
        return DurableJobService(db).get(job_id)
    except JobNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error


@router.post("/{job_id}/cancel", response_model=DurableJobCancelResponse)
def cancel_job(
    job_id: UUID,
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> DurableJobCancelResponse:
    del current_user
    try:
        job, cancellation_effective = DurableJobService(db).request_cancel(
            job_id
        )
        if cancellation_effective:
            mark_cancelled_import_target(db, job)
        return DurableJobCancelResponse(
            job=DurableJobResponse.model_validate(job),
            cancellation_effective=cancellation_effective,
        )
    except JobNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
