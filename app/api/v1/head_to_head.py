from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.head_to_head import HeadToHeadResponse
from app.services.head_to_head_service import (
    HeadToHeadDriverNotFoundError,
    HeadToHeadInvalidComparisonError,
    HeadToHeadNonRaceSessionError,
    HeadToHeadService,
    HeadToHeadSessionNotFoundError,
)


router = APIRouter(prefix="/sessions", tags=["Head to Head"])


@router.get(
    "/{race_session_id}/head-to-head",
    response_model=HeadToHeadResponse,
    summary="Compare two drivers across a race session",
)
def get_head_to_head(
    race_session_id: UUID,
    driver_a: str = Query(..., min_length=1, max_length=20),
    driver_b: str = Query(..., min_length=1, max_length=20),
    db: Session = Depends(get_db),
) -> HeadToHeadResponse:
    try:
        return HeadToHeadService(db).compare(
            race_session_id=race_session_id,
            driver_a_number=driver_a,
            driver_b_number=driver_b,
        )

    except (
        HeadToHeadSessionNotFoundError,
        HeadToHeadDriverNotFoundError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except (
        HeadToHeadInvalidComparisonError,
        HeadToHeadNonRaceSessionError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error