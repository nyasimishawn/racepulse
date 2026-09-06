from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.rate_limit import expensive_request_rate_limit
from app.core.security import AuthenticatedUser, require_roles
from app.db.database import get_db
from app.models.durable_job import DurableJobType
from app.schemas.durable_job import DurableJobResponse
from app.schemas.race_context import (
    PitEventResponse,
    RaceContextImportResponse,
    RaceControlEventResponse,
    SessionTimelineResponse,
    TimelineEventType,
    WeatherSampleResponse,
)
from app.services.race_context_service import (
    RaceContextImportError,
    RaceContextService,
    RaceContextSessionNotFoundError,
    RaceContextValidationError,
)
from app.services.job_dispatch_service import enqueue_job

router = APIRouter(
    prefix="/sessions/{race_session_id}",
    tags=["Race Context"],
)


@router.post(
    "/context/import",
    response_model=RaceContextImportResponse,
    summary="Legacy synchronous FastF1 race-context import",
    deprecated=True,
)
def import_context(
    race_session_id: UUID,
    _: None = Depends(expensive_request_rate_limit),
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> RaceContextImportResponse:
    del current_user
    try:
        return RaceContextService(db).import_context(
            race_session_id
        )

    except RaceContextSessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except RaceContextImportError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        ) from error


@router.get(
    "/weather",
    response_model=list[WeatherSampleResponse],
    summary="Get imported weather samples",
)
def get_weather(
    race_session_id: UUID,
    start_ms: int | None = Query(default=None),
    end_ms: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[WeatherSampleResponse]:
    try:
        return RaceContextService(db).list_weather(
            race_session_id=race_session_id,
            start_ms=start_ms,
            end_ms=end_ms,
        )

    except RaceContextSessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except RaceContextValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error


@router.get(
    "/race-control",
    response_model=list[RaceControlEventResponse],
    summary="Get imported race-control messages",
)
def get_race_control(
    race_session_id: UUID,
    driver_number: str | None = Query(default=None),
    start_ms: int | None = Query(default=None),
    end_ms: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[RaceControlEventResponse]:
    try:
        return RaceContextService(db).list_race_control(
            race_session_id=race_session_id,
            driver_number=driver_number,
            start_ms=start_ms,
            end_ms=end_ms,
        )

    except RaceContextSessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except RaceContextValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error


@router.get(
    "/pit-events",
    response_model=list[PitEventResponse],
    summary="Derive pit-lane entry and exit events from stored laps",
)
def get_pit_events(
    race_session_id: UUID,
    driver_number: str | None = Query(default=None),
    include_unpaired: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> list[PitEventResponse]:
    try:
        return RaceContextService(db).list_pit_events(
            race_session_id=race_session_id,
            driver_number=driver_number,
            include_unpaired=include_unpaired,
        )

    except RaceContextSessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error


@router.get(
    "/timeline",
    response_model=SessionTimelineResponse,
    summary="Get a unified race timeline",
)
def get_timeline(
    race_session_id: UUID,
    event_type: list[TimelineEventType] | None = Query(
        default=None,
        description=(
            "Repeat this parameter to filter event types, for example "
            "event_type=RACE_CONTROL&event_type=PIT_ENTRY."
        ),
    ),
    driver_number: str | None = Query(default=None),
    start_ms: int | None = Query(default=None),
    end_ms: int | None = Query(default=None),
    include_weather: bool = Query(default=False),
    limit: int = Query(default=500, ge=1, le=2_000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> SessionTimelineResponse:
    try:
        return RaceContextService(db).get_timeline(
            race_session_id=race_session_id,
            event_types=set(event_type) if event_type else None,
            driver_number=driver_number,
            start_ms=start_ms,
            end_ms=end_ms,
            include_weather=include_weather,
            limit=limit,
            offset=offset,
        )

    except RaceContextSessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except RaceContextValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error


@router.post(
    "/context/import-jobs",
    response_model=DurableJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue race-context import work",
)
def queue_context_import(
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
    return enqueue_job(
        db,
        job_type=DurableJobType.RACE_CONTEXT_IMPORT,
        target_id=race_session_id,
        idempotency_key=(
            f"race-context-import:{idempotency_key}"
            if idempotency_key
            else None
        ),
        payload={"race_session_id": str(race_session_id)},
    )
