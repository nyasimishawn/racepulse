from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.redis import RedisUnavailableError, ensure_redis_available
from app.db.database import get_db

router = APIRouter(prefix="/health", tags=["Health"])


class HealthResponse(BaseModel):
    status: str
    service: str
    environment: str
    timestamp: datetime


class HealthReadinessResponse(HealthResponse):
    database: str
    redis: str


@router.get("", response_model=HealthResponse, summary="Check API health")
async def get_health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        environment=settings.environment,
        timestamp=datetime.now(UTC),
    )


@router.get("/live", response_model=HealthResponse, summary="Check liveness")
async def get_liveness() -> HealthResponse:
    return await get_health()


@router.get(
    "/ready",
    response_model=HealthReadinessResponse,
    summary="Check database and Redis readiness",
)
async def get_readiness(
    request: Request,
    db: Session = Depends(get_db),
) -> HealthReadinessResponse:
    database_status = "ok"
    redis_status = "ok"

    try:
        db.execute(text("SELECT 1"))
    except Exception:
        database_status = "unavailable"

    redis = getattr(request.app.state, "redis", None)
    try:
        if redis is None:
            raise RedisUnavailableError("Redis client has not been configured.")
        await ensure_redis_available(redis)
    except RedisUnavailableError:
        redis_status = "unavailable"

    if database_status != "ok" or redis_status != "ok":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": "RacePulse dependencies are not ready.",
                "database": database_status,
                "redis": redis_status,
            },
        )

    return HealthReadinessResponse(
        status="ok",
        service=settings.app_name,
        environment=settings.environment,
        timestamp=datetime.now(UTC),
        database=database_status,
        redis=redis_status,
    )
