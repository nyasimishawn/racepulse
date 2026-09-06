from collections.abc import Generator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.rate_limit import expensive_request_rate_limit
from app.core.config import settings
from app.core.redis import create_sync_redis_client
from app.core.security import (
    AuthenticatedUser,
    get_active_current_user,
    require_roles,
)
from app.db.database import get_db
from app.models.weekend_download import WeekendDownload
from app.providers.base_provider import ProviderError
from app.schemas.weekend import (
    WeekendDownloadResponse,
    WeekendSelectionRequest,
)
from app.services.weekend_download_service import WeekendDownloadService
from app.workers.job_queue import RedisStreamJobPublisher


router = APIRouter(prefix="/weekends", tags=["Race Weekends"])


def get_weekend_service(
    db: Session = Depends(get_db),
) -> Generator[WeekendDownloadService, None, None]:
    redis = create_sync_redis_client()
    try:
        yield WeekendDownloadService(
            db, publisher=RedisStreamJobPublisher(redis)
        )
    finally:
        redis.close()


def _selection_response(response: Response, result: WeekendDownloadResponse):
    response.status_code = (
        202
        if result.status
        in {
            "QUEUED",
            "RUNNING",
            "RETRY_WAIT",
            "CANCEL_REQUESTED",
        }
        else 200
    )
    response.headers["Location"] = (
        f"{settings.api_v1_prefix}/weekends/{result.id}"
    )
    return result


@router.post(
    "/select",
    response_model=WeekendDownloadResponse,
    status_code=202,
    responses={200: {"model": WeekendDownloadResponse}},
    summary="Select a weekend and download all available data once",
)
def select_weekend(
    payload: WeekendSelectionRequest,
    response: Response,
    _: None = Depends(expensive_request_rate_limit),
    current_user: AuthenticatedUser = Depends(get_active_current_user),
    service: WeekendDownloadService = Depends(get_weekend_service),
) -> WeekendDownloadResponse:
    del current_user
    try:
        result = service.select_weekend(payload)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except ProviderError as error:
        raise HTTPException(
            502, "The race schedule is unavailable."
        ) from error
    return _selection_response(response, result)


@router.get("", response_model=list[WeekendDownloadResponse])
def list_downloaded_weekends(
    year: int | None = Query(default=None, ge=2018, le=2100),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[WeekendDownloadResponse]:
    statement = select(WeekendDownload)
    if year is not None:
        statement = statement.where(WeekendDownload.year == year)
    weekends = db.scalars(
        statement.order_by(
            WeekendDownload.year.desc(),
            WeekendDownload.round_number,
        )
        .limit(limit)
        .offset(offset)
    ).all()
    service = WeekendDownloadService(db)
    return [service.response(weekend) for weekend in weekends]


@router.get("/{weekend_id}", response_model=WeekendDownloadResponse)
def get_weekend_download(
    weekend_id: UUID,
    db: Session = Depends(get_db),
) -> WeekendDownloadResponse:
    service = WeekendDownloadService(db)
    try:
        return service.response(service.get(weekend_id))
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@router.post("/{weekend_id}/retry", response_model=WeekendDownloadResponse)
def retry_weekend_download(
    weekend_id: UUID,
    response: Response,
    _: None = Depends(expensive_request_rate_limit),
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    service: WeekendDownloadService = Depends(get_weekend_service),
) -> WeekendDownloadResponse:
    del current_user
    try:
        return _selection_response(response, service.retry(weekend_id))
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
