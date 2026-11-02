"""One shared OpenF1 collector. Run: python -m app.workers.live_worker."""

import asyncio
from contextlib import suppress
import logging
import sys
import time
from uuid import uuid4

import httpx

from app.core.config import settings
from app.core.redis import create_redis_client
from app.providers.openf1_provider import (
    OpenF1Provider,
    TOPICS,
    active_session,
)
from app.services.live_timing import (
    LEASE_KEY,
    RELEASE,
    RENEW,
    LiveTimingState,
    publish,
    utc_now,
)

log = logging.getLogger(__name__)


class LiveCollector:
    def __init__(self, redis, provider, owner):
        self.redis, self.provider, self.owner = redis, provider, owner
        self.state = LiveTimingState()
        self._write_lock = asyncio.Lock()

    async def emit(self, event=None):
        async with self._write_lock:
            await publish(self.redis, self.state, self.owner, event)

    async def heartbeat(self):
        while True:
            await asyncio.sleep(10)
            if not await self.redis.eval(RENEW, 1, LEASE_KEY, self.owner):
                raise RuntimeError("Live collector lease lost")
            await self.emit()

    async def collect(self):
        backoff = 1
        while True:
            try:
                now = utc_now()
                rows = await self.provider.records("sessions", year=now.year)
                session = active_session(rows, now)
                old_key = (self.state.state["session"] or {}).get(
                    "session_key"
                )
                key = (session or {}).get("session_key")
                if key != old_key:
                    self.state = LiveTimingState(session)
                if not session:
                    self.state.status(
                        "waiting", "No session is live right now."
                    )
                    await self.emit()
                    await asyncio.sleep(60)
                    continue
                self.state.status(
                    "connecting", "Connecting to the live timing feed."
                )
                await self.emit()
                async with self.provider.stream() as mqtt:
                    # Subscribe first so bootstrap cannot create a missing-event gap.
                    # Rebuild on reconnect: REST rows have no MQTT revision IDs,
                    # and must repair updates missed while disconnected.
                    restored = LiveTimingState(session)
                    for topic in TOPICS:
                        if topic == "sessions":
                            continue
                        records = await self.provider.records(
                            topic, session_key=key
                        )
                        for row in sorted(
                            records,
                            key=lambda r: (
                                r.get("date") or r.get("date_start") or "",
                                int(
                                    r.get("lap_number")
                                    or r.get("stint_number")
                                    or 0
                                ),
                            ),
                        ):
                            restored.apply(topic, row, bootstrap=True)
                    self.state = restored
                    self.state.status(
                        "waiting", "Connected; waiting for a timing update."
                    )
                    await self.emit()
                    backoff = 1
                    # Token renewal and session rediscovery bound the connection;
                    # messages themselves have no timer or release buffer.
                    expires = (
                        time.monotonic()
                        + self.provider.token_seconds_remaining
                    )
                    check_at = time.monotonic() + 60
                    iterator = mqtt.messages.__aiter__()
                    while time.monotonic() < expires:
                        if time.monotonic() >= check_at:
                            current = active_session(
                                await self.provider.records(
                                    "sessions", year=utc_now().year
                                ),
                                utc_now(),
                            )
                            if (current or {}).get("session_key") != key:
                                break
                            check_at = time.monotonic() + 60
                        try:
                            message = await asyncio.wait_for(
                                anext(iterator), timeout=5
                            )
                        except asyncio.TimeoutError:
                            continue
                        decoded = self.provider.decode(message)
                        if decoded is None:
                            continue
                        topic, row = decoded
                        if self.state.apply(topic, row):
                            await self.emit(
                                {
                                    "topic": topic,
                                    "data": row,
                                    "received_at": self.state.state[
                                        "last_received_at"
                                    ],
                                }
                            )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # Do not log provider responses, tokens or credentials.
                log.warning(
                    "Live feed reconnecting (%s)", type(error).__name__
                )
                self.state.status(
                    "reconnecting", "The live timing feed is reconnecting."
                )
                await self.emit()
                await asyncio.sleep(backoff)
                backoff = min(60, backoff * 2)

    async def run(self):
        await self.emit()
        tasks = [
            asyncio.create_task(self.heartbeat()),
            asyncio.create_task(self.collect()),
        ]
        try:
            done, _ = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def main():
    if not settings.openf1_live_configured:
        log.info(
            "Live timing disabled; configure backend OpenF1 credentials to enable it."
        )
        # Docker's service stays idle until configured/restarted.
        await asyncio.Event().wait()
        return
    redis = create_redis_client()
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            while True:
                owner = uuid4().hex
                try:
                    if await redis.set(LEASE_KEY, owner, nx=True, ex=30):
                        try:
                            await LiveCollector(
                                redis, OpenF1Provider(settings, client), owner
                            ).run()
                        finally:
                            with suppress(Exception):
                                await redis.eval(RELEASE, 1, LEASE_KEY, owner)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    log.warning(
                        "Live collector unavailable (%s)", type(error).__name__
                    )
                await asyncio.sleep(10)
    finally:
        await redis.aclose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
