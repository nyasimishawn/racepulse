from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.qualifying import (
    QualifyingSummaryResponse,
    RaceQualifyingReferenceResponse,
)
from app.services.qualifying_service import (
    NotQualifyingSessionError,
    QualifyingQueryService,
    QualifyingSessionNotFoundError,
    RaceDriverNotFoundError,
    RaceSessionNotFoundError,
)

router = APIRouter(prefix="/sessions", tags=["Qualifying"])


@router.get(
    "/{qualifying_session_id}/qualifying-summary",
    response_model=QualifyingSummaryResponse,
    summary="Get Q1, Q2, Q3 and best laps for a qualifying session",
)
def get_qualifying_summary(
    qualifying_session_id: UUID,
    db: Session = Depends(get_db),
) -> QualifyingSummaryResponse:
    try:
        return QualifyingQueryService(db).get_qualifying_summary(
            qualifying_session_id
        )

    except QualifyingSessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except NotQualifyingSessionError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error


@router.get(
    "/{race_session_id}/drivers/{driver_number}/qualifying-reference",
    response_model=RaceQualifyingReferenceResponse,
    summary="Get a driver's best qualifying reference lap for a race",
)
def get_qualifying_reference(
    race_session_id: UUID,
    driver_number: str,
    db: Session = Depends(get_db),
) -> RaceQualifyingReferenceResponse:
    try:
        return QualifyingQueryService(
            db
        ).get_race_qualifying_reference(
            race_session_id=race_session_id,
            driver_number=driver_number,
        )

    except RaceSessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except RaceDriverNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error