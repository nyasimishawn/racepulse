from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.tyre_insight import (
    LiftCoastInsightResponse,
    TyreInsightResponse,
)
from app.services.tyre_insight_service import (
    RaceEngineeringDriverNotFoundError,
    RaceEngineeringInsightService,
    RaceEngineeringNonRaceSessionError,
    RaceEngineeringSessionNotFoundError,
)

router = APIRouter(
    prefix="/sessions/{race_session_id}/drivers/{driver_number}",
    tags=["Race Engineering Insights"],
)


@router.get(
    "/tyre-insights",
    response_model=TyreInsightResponse,
    summary="Score race pace against same-compound driver baselines",
)
def get_driver_tyre_insights(
    race_session_id: UUID,
    driver_number: str,
    db: Session = Depends(get_db),
) -> TyreInsightResponse:
    try:
        return RaceEngineeringInsightService(db).get_tyre_insights(
            race_session_id=race_session_id,
            driver_number=driver_number,
        )
    except (
        RaceEngineeringSessionNotFoundError,
        RaceEngineeringDriverNotFoundError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except RaceEngineeringNonRaceSessionError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error


@router.get(
    "/lift-and-coast",
    response_model=LiftCoastInsightResponse,
    summary="Infer lift-and-coast patterns from stored telemetry",
)
def get_driver_lift_and_coast_insights(
    race_session_id: UUID,
    driver_number: str,
    start_lap: int | None = Query(default=None, ge=1),
    end_lap: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
) -> LiftCoastInsightResponse:
    try:
        return RaceEngineeringInsightService(
            db
        ).get_lift_and_coast_insights(
            race_session_id=race_session_id,
            driver_number=driver_number,
            start_lap=start_lap,
            end_lap=end_lap,
        )
    except (
        RaceEngineeringSessionNotFoundError,
        RaceEngineeringDriverNotFoundError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except RaceEngineeringNonRaceSessionError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
