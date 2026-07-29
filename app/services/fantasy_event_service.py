import json
from datetime import UTC, datetime
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError


def fantasy_event_channel(race_session_id: UUID) -> str:
    return f"racepulse:fantasy:{race_session_id}"


async def publish_fantasy_event(
    redis: Redis | None,
    *,
    race_session_id: UUID,
    event_type: str,
    payload: dict[str, object],
) -> None:
    if redis is None:
        return

    event = {
        "type": event_type,
        "race_session_id": str(race_session_id),
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": payload,
    }

    try:
        await redis.publish(
            fantasy_event_channel(race_session_id),
            json.dumps(event),
        )
    except RedisError:
        # REST scoring must still work when Redis is offline.
        return