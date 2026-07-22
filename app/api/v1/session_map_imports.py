from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.database import get_db
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
    db: Session = Depends(get_db),
) -> SessionMapImportResponse:
    try:
        return SessionMapImportService(db).create(
            race_session_id=race_session_id,
            payload=payload,
        )

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
    db: Session = Depends(get_db),
) -> SessionMapImportResponse:
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
    db: Session = Depends(get_db),
) -> list[SessionMapImportDriverResponse]:
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
    db: Session = Depends(get_db),
) -> SessionMapImportResponse:
    try:
        return SessionMapImportService(db).run(map_import_id)

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