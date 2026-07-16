from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.attack_index import AttackIndexResponse
from app.services.attack_index_service import (
    AttackIndexService,
    RaceLapNotFoundError,
)

router = APIRouter(
    prefix=(
        "/sessions/{race_session_id}/drivers/{driver_number}"
        "/laps/{race_lap_number}"
    ),
    tags=["Attack Index"],
)


@router.get(
    "/attack-index",
    response_model=AttackIndexResponse,
    summary="Estimate attack intensity against a qualifying reference lap",
)
def get_attack_index(
    race_session_id: UUID,
    driver_number: str,
    race_lap_number: int,
    db: Session = Depends(get_db),
) -> AttackIndexResponse:
    try:
        return AttackIndexService(db).assess(
            race_session_id=race_session_id,
            driver_number=driver_number,
            race_lap_number=race_lap_number,
        )

    except RaceLapNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error