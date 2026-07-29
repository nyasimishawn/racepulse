from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.database import get_db
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
    db: Session = Depends(get_db),
) -> SessionTelemetryImportResponse:
    try:
        return SessionTelemetryImportService(db).create(
            race_session_id=race_session_id,
            payload=payload,
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
    db: Session = Depends(get_db),
) -> SessionTelemetryImportResponse:
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
    db: Session = Depends(get_db),
) -> list[SessionTelemetryImportDriverResponse]:
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
    db: Session = Depends(get_db),
) -> SessionTelemetryImportResponse:
    try:
        return SessionTelemetryImportService(db).run(
            telemetry_import_id
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