from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.strategy import RaceStrategyResponse
from app.services.strategy_service import (
    NonRaceStrategySessionError,
    StrategyQueryService,
    StrategySessionNotFoundError,
)

router = APIRouter(
    prefix="/sessions/{race_session_id}/strategy",
    tags=["Strategy"],
)


@router.get(
    "",
    response_model=RaceStrategyResponse,
    summary="Get tyre stints and clean-lap pace context for a race",
)
def get_race_strategy(
    race_session_id: UUID,
    driver_number: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> RaceStrategyResponse:
    try:
        return StrategyQueryService(db).get_race_strategy(
            race_session_id=race_session_id,
            driver_number=driver_number,
        )

    except StrategySessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except NonRaceStrategySessionError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error