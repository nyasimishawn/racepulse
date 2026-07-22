from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.core.exceptions import install_exception_handlers
from app.core.redis import (
    RedisUnavailableError,
    create_redis_client,
    ensure_redis_available,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis = create_redis_client()
    app.state.redis = redis

    try:
        await ensure_redis_available(redis)
        logger.info("Redis is connected.")
    except RedisUnavailableError as error:
        logger.warning("Redis is not ready: %s", error)

    logger.info(
        "%s is starting in %s mode",
        settings.app_name,
        settings.environment,
    )

    yield

    await redis.aclose()

    logger.info("%s is shutting down", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "RacePulse imports, normalizes, analyzes, and replays public "
        "Formula 1 timing and telemetry data."
    ),
    debug=settings.debug,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=settings.cors_allowed_origin_regex,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

install_exception_handlers(app)

app.include_router(
    api_router,
    prefix=settings.api_v1_prefix,
)


@app.get("/", tags=["Root"], summary="Show API information")
async def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "docs": "/docs",
        "health": f"{settings.api_v1_prefix}/health",
    }