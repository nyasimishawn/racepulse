from __future__ import annotations

from collections import defaultdict, deque
from datetime import UTC, datetime
from math import ceil
from threading import Lock

from fastapi import HTTPException, Request, status
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings


_fallback_events: dict[str, deque[float]] = defaultdict(deque)
_fallback_lock = Lock()


async def registration_rate_limit(request: Request) -> None:
    await enforce_rate_limit(
        request,
        bucket="registration",
        limit=settings.registration_rate_limit,
        window_seconds=settings.registration_rate_window_seconds,
    )


async def expensive_request_rate_limit(request: Request) -> None:
    await enforce_rate_limit(
        request,
        bucket="expensive",
        limit=settings.expensive_request_rate_limit,
        window_seconds=settings.expensive_request_rate_window_seconds,
    )


async def enforce_rate_limit(
    request: Request,
    *,
    bucket: str,
    limit: int,
    window_seconds: int,
) -> None:
    """Use Redis where available, with a bounded in-process fallback."""
    if limit <= 0 or window_seconds <= 0:
        return

    client_key = _client_key(request)
    now = datetime.now(UTC).timestamp()
    window = int(now // window_seconds)
    key = f"racepulse:rate-limit:{bucket}:{client_key}:{window}"
    redis: Redis | None = getattr(request.app.state, "redis", None)

    if redis is not None:
        try:
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, window_seconds)
            remaining = await redis.ttl(key)
            if count <= limit:
                return
            _raise_limited(max(1, remaining))
        except RedisError:
            # Redis recovery is handled independently; retain conservative
            # process-local protection in the meantime.
            pass

    allowed, retry_after = await _fallback_allow(
        key=f"{bucket}:{client_key}",
        now=now,
        limit=limit,
        window_seconds=window_seconds,
    )
    if not allowed:
        _raise_limited(retry_after)


async def _fallback_allow(
    *,
    key: str,
    now: float,
    limit: int,
    window_seconds: int,
) -> tuple[bool, int]:
    with _fallback_lock:
        events = _fallback_events[key]
        oldest_allowed = now - window_seconds
        while events and events[0] <= oldest_allowed:
            events.popleft()

        if len(events) >= limit:
            return False, max(1, ceil(events[0] + window_seconds - now))

        events.append(now)
        return True, window_seconds


def reset_fallback_rate_limits() -> None:
    """Test helper; production code should never need to call this."""
    _fallback_events.clear()


def _client_key(request: Request) -> str:
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            value = forwarded.split(",", maxsplit=1)[0].strip()
            if value:
                return value[:128]

    if request.client is not None and request.client.host:
        return request.client.host[:128]

    return "unknown"


def _raise_limited(retry_after: int) -> None:
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many requests. Try again later.",
        headers={"Retry-After": str(max(1, retry_after))},
    )
