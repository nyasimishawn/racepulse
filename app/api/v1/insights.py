from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.insight import RacePaceInsightResponse
from app.services.insight_service import (
    InsightDriverNotFoundError,
    InsightSessionNotFoundError,
    NonRaceInsightSessionError,
    RaceInsightService,
)

router = APIRouter(
    prefix="/sessions/{race_session_id}/insights",
    tags=["Race Engineering Insights"],
)


@router.get(
    "/race-pace",
    response_model=RacePaceInsightResponse,
    summary="Analyse clean race pace, stint trends, consistency, and sectors",
)
def get_race_pace_insights(
    race_session_id: UUID,
    driver_number: str | None = Query(default=None),
    include_lap_series: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> RacePaceInsightResponse:
    try:
        return RaceInsightService(db).get_race_pace_insights(
            race_session_id=race_session_id,
            driver_number=driver_number,
            include_lap_series=include_lap_series,
        )

    except InsightSessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except InsightDriverNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except NonRaceInsightSessionError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error