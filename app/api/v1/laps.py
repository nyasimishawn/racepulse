from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    status,
)
from sqlalchemy.orm import Session

from app.core.rate_limit import expensive_request_rate_limit
from app.core.security import AuthenticatedUser, require_roles
from app.db.database import get_db
from app.models.durable_job import DurableJobType
from app.schemas.durable_job import DurableJobResponse
from app.schemas.lap import LapImportResponse, LapResponse
from app.services.job_dispatch_service import enqueue_job
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
    summary="Legacy synchronous bulk lap import",
    deprecated=True,
)
def import_laps(
    race_session_id: UUID,
    _: None = Depends(expensive_request_rate_limit),
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> LapImportResponse:
    del current_user
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


@router.post(
    "/import-jobs",
    response_model=DurableJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue bulk lap import work",
)
def queue_lap_import(
    race_session_id: UUID,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        max_length=255,
    ),
    _: None = Depends(expensive_request_rate_limit),
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> DurableJobResponse:
    del current_user
    if not LapQueryService(db).session_exists(race_session_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Race session not found.",
        )
    return enqueue_job(
        db,
        job_type=DurableJobType.SESSION_LAPS_IMPORT,
        target_id=race_session_id,
        idempotency_key=(
            f"session-laps-import:{idempotency_key}"
            if idempotency_key
            else None
        ),
        payload={"race_session_id": str(race_session_id)},
    )


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
