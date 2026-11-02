"""Server-only OpenF1 REST bootstrap and push subscriptions (no polling feed)."""

import asyncio
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
import ssl
import time

import aiomqtt
import httpx

from app.core.config import Settings

TOPICS = (
    "drivers",
    "position",
    "intervals",
    "laps",
    "stints",
    "pit",
    "race_control",
    "weather",
    "sessions",
)


def utc_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (
            parsed.replace(tzinfo=timezone.utc)
            if parsed.tzinfo is None
            else parsed
        )
    except (ValueError, TypeError):
        return None


def active_session(rows: list[dict], now: datetime) -> dict | None:
    """Pin an exact key, never interpret the last historical session as live."""
    candidates = []
    for row in rows:
        start, end = (
            utc_date(row.get("date_start")),
            utc_date(row.get("date_end")),
        )
        if (
            row.get("is_cancelled")
            or not row.get("session_key")
            or not start
            or not end
        ):
            continue
        if start - timedelta(minutes=30) <= now <= end + timedelta(minutes=30):
            candidates.append(row)
    # Prefer a session already underway over a later session's pre-start window.
    started = [row for row in candidates if utc_date(row["date_start"]) <= now]
    return max(
        started or candidates, key=lambda row: row["date_start"], default=None
    )


class OpenF1Provider:
    def __init__(self, config: Settings, client: httpx.AsyncClient):
        self.config = config
        self.client = client
        self._token = ""
        self._expires = 0.0
        self._requests: deque[float] = deque()
        self._last_request = 0.0

    async def _pace(self):
        # Reserve headroom under the authenticated 60/min, 6/sec limits.
        while True:
            now = time.monotonic()
            while self._requests and self._requests[0] <= now - 60:
                self._requests.popleft()
            delay = max(0.0, self._last_request + 0.25 - now)
            if len(self._requests) >= 55:
                delay = max(delay, self._requests[0] + 60 - now)
            if delay <= 0:
                self._last_request = now
                self._requests.append(now)
                return
            await asyncio.sleep(delay)

    async def token(self, *, force: bool = False) -> str:
        if not force and self._token and time.monotonic() < self._expires:
            return self._token
        await self._pace()
        response = await self.client.post(
            "https://api.openf1.org/token",
            data={
                "username": self.config.openf1_username,
                "password": self.config.openf1_password.get_secret_value(),
            },
        )
        response.raise_for_status()
        payload = response.json()
        self._token = payload["access_token"]
        # Refresh early and reconnect MQTT before its token expires.
        self._expires = time.monotonic() + max(
            1, int(payload["expires_in"]) - 120
        )
        return self._token

    async def records(self, topic: str, **filters) -> list[dict]:
        if topic not in TOPICS:
            raise ValueError("Unsupported live topic")
        for attempt in range(3):
            token = await self.token()
            await self._pace()
            response = await self.client.get(
                f"https://api.openf1.org/v1/{topic}",
                params=filters,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code == 401 and attempt < 2:
                self._expires = 0
                continue
            if response.status_code == 429 and attempt < 2:
                try:
                    delay = float(response.headers.get("Retry-After", "60"))
                except ValueError:
                    delay = 60
                await asyncio.sleep(min(120, max(1, delay)))
                continue
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("Unexpected OpenF1 response")
            return payload
        raise RuntimeError("OpenF1 request failed")

    @asynccontextmanager
    async def stream(self):
        token = await self.token()
        async with aiomqtt.Client(
            hostname="mqtt.openf1.org",
            port=8883,
            username=self.config.openf1_username,
            password=token,
            tls_context=ssl.create_default_context(),
            keepalive=30,
            max_queued_incoming_messages=10000,
        ) as client:
            await client.subscribe([(f"v1/{topic}", 1) for topic in TOPICS])
            yield client

    @staticmethod
    def decode(message) -> tuple[str, dict] | None:
        topic = str(message.topic).removeprefix("v1/")
        if topic not in TOPICS:
            return None
        try:
            payload = json.loads(message.payload)
        except (ValueError, TypeError, UnicodeDecodeError):
            return None
        return (topic, payload) if isinstance(payload, dict) else None

    @property
    def token_seconds_remaining(self) -> float:
        return max(1, self._expires - time.monotonic())
