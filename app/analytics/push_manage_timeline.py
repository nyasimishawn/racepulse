from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.push_manage_timeline import (
    PushManageTimelineResponse,
)
from app.services.push_manage_timeline_service import (
    NonRacePushManageSessionError,
    PushManageDriverNotFoundError,
    PushManageSessionNotFoundError,
    PushManageTimelineService,
    PushManageValidationError,
)

router = APIRouter(
    prefix=(
        "/sessions/{race_session_id}/drivers/{driver_number}"
        "/push-manage-timeline"
    ),
    tags=["Push / Manage Timeline"],
)


@router.get(
    "",
    response_model=PushManageTimelineResponse,
    summary="Show telemetry-derived pushing and management patterns by lap",
)
def get_push_manage_timeline(
    race_session_id: UUID,
    driver_number: str,
    start_lap: int | None = Query(default=None, ge=1),
    end_lap: int | None = Query(default=None, ge=1),
    include_context: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> PushManageTimelineResponse:
    try:
        return PushManageTimelineService(db).get_timeline(
            race_session_id=race_session_id,
            driver_number=driver_number,
            start_lap=start_lap,
            end_lap=end_lap,
            include_context=include_context,
        )

    except (
        PushManageSessionNotFoundError,
        PushManageDriverNotFoundError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except (
        NonRacePushManageSessionError,
        PushManageValidationError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error