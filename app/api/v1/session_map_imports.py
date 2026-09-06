from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.rate_limit import expensive_request_rate_limit
from app.core.security import AuthenticatedUser, require_roles
from app.db.database import get_db
from app.models.durable_job import DurableJobType
from app.models.session_map_import import SessionMapImport
from app.schemas.session_map_import import (
    SessionMapCoverageResponse,
    SessionMapImportCreate,
    SessionMapImportDriverResponse,
    SessionMapImportResponse,
)
from app.services.session_map_import_service import (
    SessionMapImportError,
    SessionMapImportNotFoundError,
    SessionMapImportService,
    SessionMapImportStateError,
    SessionMapSourceSessionNotFoundError,
)
from app.services.job_dispatch_service import enqueue_job


session_router = APIRouter(tags=["Session Map Imports"])
job_router = APIRouter(
    prefix="/map-imports",
    tags=["Session Map Imports"],
)


@session_router.post(
    "/sessions/{race_session_id}/map-imports",
    response_model=SessionMapImportResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create an all-driver session map import",
)
def create_map_import(
    race_session_id: UUID,
    payload: SessionMapImportCreate,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        max_length=255,
    ),
    _: None = Depends(expensive_request_rate_limit),
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> SessionMapImportResponse:
    del current_user
    try:
        map_import = SessionMapImportService(db).create(
            race_session_id=race_session_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )
        return _queue_map_import(db, map_import, idempotency_key)

    except SessionMapSourceSessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except SessionMapImportStateError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error


@session_router.get(
    "/sessions/{race_session_id}/map-coverage",
    response_model=SessionMapCoverageResponse,
    summary="Get map-data coverage for a session",
)
def get_map_coverage(
    race_session_id: UUID,
    db: Session = Depends(get_db),
) -> SessionMapCoverageResponse:
    try:
        return SessionMapImportService(db).get_coverage(
            race_session_id
        )

    except SessionMapSourceSessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error


@job_router.get(
    "/{map_import_id}",
    response_model=SessionMapImportResponse,
    summary="Get session map import progress",
)
def get_map_import(
    map_import_id: UUID,
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> SessionMapImportResponse:
    del current_user
    try:
        return SessionMapImportService(db).get(map_import_id)

    except SessionMapImportNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error


@job_router.get(
    "/{map_import_id}/drivers",
    response_model=list[SessionMapImportDriverResponse],
    summary="Get per-driver map import coverage",
)
def get_map_import_drivers(
    map_import_id: UUID,
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> list[SessionMapImportDriverResponse]:
    del current_user
    try:
        return SessionMapImportService(db).list_drivers(
            map_import_id
        )

    except SessionMapImportNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error


@job_router.post(
    "/{map_import_id}/run",
    response_model=SessionMapImportResponse,
    summary="Run a session map import",
)
def run_map_import(
    map_import_id: UUID,
    _: None = Depends(expensive_request_rate_limit),
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> SessionMapImportResponse:
    del current_user
    try:
        map_import = SessionMapImportService(db).get(map_import_id)
        return _queue_map_import(
            db,
            map_import,
            map_import.idempotency_key,
        )

    except SessionMapImportNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except SessionMapImportStateError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error

    except SessionMapImportError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        ) from error


def _queue_map_import(
    db: Session,
    map_import: SessionMapImport,
    idempotency_key: str | None,
) -> SessionMapImport:
    durable_job = enqueue_job(
        db,
        job_type=DurableJobType.SESSION_MAP_IMPORT,
        target_id=map_import.id,
        idempotency_key=(
            f"session-map-import:{idempotency_key}"
            if idempotency_key
            else None
        ),
        payload={"map_import_id": str(map_import.id)},
    )
    if map_import.durable_job_id != durable_job.id:
        map_import.durable_job_id = durable_job.id
        db.commit()
        db.refresh(map_import)
    return map_import
