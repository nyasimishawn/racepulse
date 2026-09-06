from __future__ import annotations

from uuid import UUID

from redis import Redis
from redis.exceptions import ResponseError

from app.core.config import settings


class RedisStreamJobPublisher:
    """Thin adapter for the single RacePulse Redis Streams transport."""

    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    def ensure_consumer_group(self) -> None:
        try:
            self.redis.xgroup_create(
                settings.job_stream_key,
                settings.job_consumer_group,
                id="0-0",
                mkstream=True,
            )
        except ResponseError as error:
            if "BUSYGROUP" not in str(error):
                raise

    def publish(self, job_id: UUID) -> str:
        return str(
            self.redis.xadd(
                settings.job_stream_key,
                {"job_id": str(job_id)},
            )
        )
