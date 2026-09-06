from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.rate_limit import expensive_request_rate_limit
from app.core.security import AuthenticatedUser, require_roles
from app.db.database import get_db
from app.models.durable_job import DurableJobType
from app.models.session_telemetry_import import SessionTelemetryImport
from app.schemas.session_telemetry_import import (
    SessionTelemetryCoverageResponse,
    SessionTelemetryImportCreate,
    SessionTelemetryImportDriverResponse,
    SessionTelemetryImportResponse,
)
from app.services.session_telemetry_import_service import (
    SessionTelemetryImportError,
    SessionTelemetryImportNotFoundError,
    SessionTelemetryImportService,
    SessionTelemetryImportStateError,
    SessionTelemetrySourceSessionNotFoundError,
)
from app.services.job_dispatch_service import enqueue_job


session_router = APIRouter(tags=["Session Telemetry Imports"])

job_router = APIRouter(
    prefix="/telemetry-imports",
    tags=["Session Telemetry Imports"],
)


@session_router.post(
    "/sessions/{race_session_id}/telemetry-imports",
    response_model=SessionTelemetryImportResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create a bounded multi-driver telemetry import",
)
def create_session_telemetry_import(
    race_session_id: UUID,
    payload: SessionTelemetryImportCreate,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        max_length=255,
    ),
    _: None = Depends(expensive_request_rate_limit),
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> SessionTelemetryImportResponse:
    del current_user
    try:
        telemetry_import = SessionTelemetryImportService(db).create(
            race_session_id=race_session_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )
        return _queue_telemetry_import(
            db,
            telemetry_import,
            idempotency_key,
        )

    except SessionTelemetrySourceSessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except SessionTelemetryImportStateError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error


@session_router.get(
    "/sessions/{race_session_id}/telemetry-coverage",
    response_model=SessionTelemetryCoverageResponse,
    summary="Show stored telemetry coverage for every session driver",
)
def get_session_telemetry_coverage(
    race_session_id: UUID,
    db: Session = Depends(get_db),
) -> SessionTelemetryCoverageResponse:
    try:
        return SessionTelemetryImportService(db).get_coverage(
            race_session_id
        )

    except SessionTelemetrySourceSessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error


@job_router.get(
    "/{telemetry_import_id}",
    response_model=SessionTelemetryImportResponse,
    summary="Get bounded telemetry import progress",
)
def get_session_telemetry_import(
    telemetry_import_id: UUID,
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> SessionTelemetryImportResponse:
    del current_user
    try:
        return SessionTelemetryImportService(db).get(
            telemetry_import_id
        )

    except SessionTelemetryImportNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error


@job_router.get(
    "/{telemetry_import_id}/drivers",
    response_model=list[SessionTelemetryImportDriverResponse],
    summary="Get per-driver bounded telemetry import coverage",
)
def get_session_telemetry_import_drivers(
    telemetry_import_id: UUID,
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> list[SessionTelemetryImportDriverResponse]:
    del current_user
    try:
        return SessionTelemetryImportService(db).list_drivers(
            telemetry_import_id
        )

    except SessionTelemetryImportNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error


@job_router.post(
    "/{telemetry_import_id}/run",
    response_model=SessionTelemetryImportResponse,
    summary="Run a bounded multi-driver telemetry import",
)
def run_session_telemetry_import(
    telemetry_import_id: UUID,
    _: None = Depends(expensive_request_rate_limit),
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> SessionTelemetryImportResponse:
    del current_user
    try:
        telemetry_import = SessionTelemetryImportService(db).get(
            telemetry_import_id
        )
        return _queue_telemetry_import(
            db,
            telemetry_import,
            telemetry_import.idempotency_key,
        )

    except SessionTelemetryImportNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except SessionTelemetryImportStateError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error

    except SessionTelemetryImportError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        ) from error


def _queue_telemetry_import(
    db: Session,
    telemetry_import: SessionTelemetryImport,
    idempotency_key: str | None,
) -> SessionTelemetryImport:
    durable_job = enqueue_job(
        db,
        job_type=DurableJobType.SESSION_TELEMETRY_IMPORT,
        target_id=telemetry_import.id,
        idempotency_key=(
            f"session-telemetry-import:{idempotency_key}"
            if idempotency_key
            else None
        ),
        payload={"telemetry_import_id": str(telemetry_import.id)},
    )
    if telemetry_import.durable_job_id != durable_job.id:
        telemetry_import.durable_job_id = durable_job.id
        db.commit()
        db.refresh(telemetry_import)
    return telemetry_import
