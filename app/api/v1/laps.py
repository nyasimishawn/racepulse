from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.lap import LapImportResponse, LapResponse
from app.services.lap_service import (
    LapImportError,
    LapImportService,
    LapQueryService,
    RaceSessionNotFoundError,
)

router = APIRouter(
    prefix="/sessions/{race_session_id}/laps",
    tags=["Laps"],
)


@router.post(
    "/import",
    response_model=LapImportResponse,
    summary="Import lap data for an existing race session",
)
def import_laps(
    race_session_id: UUID,
    db: Session = Depends(get_db),
) -> LapImportResponse:
    try:
        laps_upserted, laps_skipped = LapImportService(db).import_laps(
            race_session_id
        )

        return LapImportResponse(
            race_session_id=race_session_id,
            source="FASTF1",
            laps_upserted=laps_upserted,
            laps_skipped=laps_skipped,
        )

    except RaceSessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except LapImportError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        ) from error


@router.get(
    "",
    response_model=list[LapResponse],
    summary="List imported laps for a race session",
)
def list_laps(
    race_session_id: UUID,
    driver_number: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[LapResponse]:
    service = LapQueryService(db)

    if not service.session_exists(race_session_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Race session not found.",
        )

    return service.list_laps(
        race_session_id=race_session_id,
        driver_number=driver_number,
        limit=limit,
        offset=offset,
    )