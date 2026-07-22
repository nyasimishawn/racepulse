from fastapi import HTTPException, Request, status
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings


class RedisUnavailableError(RuntimeError):
    pass


def create_redis_client() -> Redis:
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=1,
    )


async def ensure_redis_available(redis: Redis) -> None:
    try:
        await redis.ping()
    except RedisError as error:
        raise RedisUnavailableError(
            "Redis is unavailable. Start Redis before using replay rooms."
        ) from error


async def get_redis(request: Request) -> Redis:
    redis: Redis | None = getattr(request.app.state, "redis", None)

    if redis is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis client has not been configured.",
        )

    try:
        await ensure_redis_available(redis)
    except RedisUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error

    return redis