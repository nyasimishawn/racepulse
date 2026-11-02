import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import uuid4

from fakeredis.aioredis import FakeRedis
import httpx
import pytest

from app.core.config import Settings, settings
from app.providers.openf1_provider import OpenF1Provider, active_session
from app.services import live_timing as live
from app.workers.live_worker import LiveCollector

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
SESSION = {
    "session_key": 1234,
    "session_name": "Race",
    "country_name": "Singapore",
    "date_start": (NOW - timedelta(minutes=10)).isoformat(),
    "date_end": (NOW + timedelta(hours=2)).isoformat(),
}


class Cache:
    def __init__(self, snapshot=None):
        self.snapshot = snapshot

    async def get(self, key):
        return json.dumps(self.snapshot) if self.snapshot else None


def test_session_selection_rejects_historical_cancelled_and_future():
    historical = {
        **SESSION,
        "session_key": 1,
        "date_end": (NOW - timedelta(hours=1)).isoformat(),
    }
    cancelled = {**SESSION, "session_key": 2, "is_cancelled": True}
    future = {
        **SESSION,
        "session_key": 3,
        "date_start": (NOW + timedelta(hours=1)).isoformat(),
    }
    assert active_session([historical, cancelled, future], NOW) is None
    assert active_session([historical, SESSION], NOW)["session_key"] == 1234


def test_lap_partial_updates_duplicates_wrong_session_and_out_of_order():
    state = live.LiveTimingState(SESSION)
    first = {
        "session_key": 1234,
        "driver_number": 63,
        "lap_number": 8,
        "_key": "63_8",
        "_id": 10,
        "duration_sector_1": 27.1,
    }
    assert state.apply("laps", first, received_at=NOW)
    assert not state.apply("laps", first, received_at=NOW)
    assert state.apply(
        "laps",
        {**first, "_id": 11, "duration_sector_2": 32.2},
        received_at=NOW,
    )
    assert not state.apply(
        "laps", {**first, "_id": 9, "duration_sector_1": 99}
    )
    assert not state.apply(
        "position", {"session_key": 9999, "driver_number": 63, "position": 1}
    )
    state.apply(
        "laps",
        {**first, "_key": "63_9", "_id": 12, "lap_number": 9},
        received_at=NOW,
    )
    assert not state.apply("laps", {**first, "_id": 15, "lap_duration": 90})
    driver = state.snapshot(now=NOW)["drivers"][0]
    assert driver["lap_number"] == 9
    assert driver["duration_sector_2"] is None
    assert state.state["last_received_at"] == NOW.isoformat()
    assert state.state["status"] == "live"


def test_newer_sector_version_is_merged_without_erasing_other_sectors():
    state = live.LiveTimingState(SESSION)
    row = {
        "session_key": 1234,
        "driver_number": 63,
        "lap_number": 8,
        "_key": "63_8",
        "_id": 10,
        "duration_sector_1": 27.1,
    }
    state.apply("laps", row)
    state.apply("laps", {**row, "_id": 11, "duration_sector_2": 32.2})
    assert state.snapshot()["drivers"][0]["duration_sector_1"] == 27.1
    assert state.snapshot()["drivers"][0]["duration_sector_2"] == 32.2


def test_queued_partial_lap_preserves_restored_sectors_and_last_completed_lap():
    state = live.LiveTimingState(SESSION)
    lap = {
        "session_key": 1234,
        "driver_number": 63,
        "lap_number": 8,
        "duration_sector_1": 27.1,
        "duration_sector_2": 32.2,
        "lap_duration": 90.3,
    }
    state.apply("laps", lap, bootstrap=True)
    state.apply(
        "laps",
        {
            **lap,
            "_id": 20,
            "_key": "63_8",
            "duration_sector_2": None,
            "lap_duration": None,
        },
    )
    assert state.snapshot()["drivers"][0]["duration_sector_2"] == 32.2
    state.apply(
        "laps",
        {
            **lap,
            "_id": 21,
            "_key": "63_9",
            "lap_number": 9,
            "lap_duration": None,
        },
    )
    assert state.snapshot()["drivers"][0]["last_lap_duration"] == 90.3


@pytest.mark.asyncio
async def test_reconnect_rest_snapshot_repairs_updates_missed_after_mqtt_version(
    monkeypatch,
):
    from contextlib import asynccontextmanager
    from app.workers import live_worker

    recovered = asyncio.Event()
    row = {
        "session_key": 1234,
        "driver_number": 63,
        "lap_number": 8,
        "duration_sector_1": 27.1,
    }

    class Provider:
        token_seconds_remaining = 3600
        connections = 0

        async def records(self, topic, **filters):
            return (
                [SESSION]
                if topic == "sessions"
                else [
                    {
                        **row,
                        "duration_sector_2": 32.2
                        if self.connections > 1
                        else None,
                    }
                ]
            )

        @asynccontextmanager
        async def stream(self):
            self.connections += 1

            async def messages():
                if self.connections == 1:
                    yield ("laps", {**row, "_key": "63_8", "_id": 50})
                    raise RuntimeError("Disconnected")
                await asyncio.Event().wait()
                yield None

            yield SimpleNamespace(messages=messages())

        def decode(self, message):
            return message

    async def capture(redis, state, owner, payload):
        if (
            provider.connections > 1
            and state.drivers.get(63, {}).get("duration_sector_2") == 32.2
        ):
            recovered.set()

    provider = Provider()
    monkeypatch.setattr(live_worker, "TOPICS", ("laps",))
    monkeypatch.setattr(live_worker, "utc_now", lambda: NOW)
    monkeypatch.setattr(live_worker, "publish", capture)
    task = asyncio.create_task(LiveCollector(None, provider, "test").collect())
    try:
        await asyncio.wait_for(recovered.wait(), timeout=2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_old_cache_and_old_session_are_never_labelled_live():
    state = live.LiveTimingState(SESSION)
    state.apply(
        "position",
        {"session_key": 1234, "driver_number": 63, "position": 1},
        received_at=NOW,
    )
    cached = state.snapshot(now=NOW)
    assert (await live.read_snapshot(Cache(cached), configured=True, now=NOW))[
        "status"
    ] == "live"
    assert (
        await live.read_snapshot(
            Cache(cached), configured=True, now=NOW + timedelta(seconds=40)
        )
    )["status"] == "reconnecting"
    assert (
        await live.read_snapshot(
            Cache(cached), configured=True, now=NOW + timedelta(hours=3)
        )
    )["status"] == "finished"
    missing = await live.read_snapshot(
        Cache(cached), configured=False, now=NOW
    )
    assert not missing["configured"]
    assert missing["drivers"] == []


@pytest.mark.asyncio
async def test_bootstrap_is_not_presented_as_a_fresh_push():
    state = live.LiveTimingState(SESSION)
    state.apply(
        "position",
        {"session_key": 1234, "driver_number": 63, "position": 1},
        bootstrap=True,
    )
    cached = state.snapshot(now=NOW)
    assert cached["last_received_at"] is None
    assert cached["status"] != "live"


@pytest.mark.asyncio
async def test_provider_form_auth_and_bearer_and_exact_key():
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path == "/token":
            assert request.headers["content-type"].startswith(
                "application/x-www-form-urlencoded"
            )
            assert b"username=account" in request.content
            return httpx.Response(
                200, json={"access_token": "test-token", "expires_in": "3600"}
            )
        assert request.headers["authorization"] == "Bearer test-token"
        assert request.url.params["session_key"] == "1234"
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond)
    ) as client:
        provider = OpenF1Provider(
            Settings(
                _env_file=None,
                openf1_username="account",
                openf1_password="private",
            ),
            client,
        )
        await provider.records("laps", session_key=1234)
        assert len(requests) == 2
        assert "private" not in repr(provider.config)


def test_unconfigured_api_and_websocket_are_explicit(api_client, monkeypatch):
    monkeypatch.setattr(settings, "openf1_live_enabled", False)
    response = api_client.get("/api/v1/live/current")
    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["drivers"] == []
    with api_client.websocket_connect("/api/v1/live/stream") as socket:
        assert not socket.receive_json()["data"]["configured"]


def test_websocket_origin_is_checked(api_client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as error:
        with api_client.websocket_connect(
            "/api/v1/live/stream", headers={"origin": "https://untrusted.test"}
        ):
            pass
    assert error.value.code == 1008


@pytest.mark.asyncio
async def test_push_is_published_immediately_without_a_release_timer(
    monkeypatch,
):
    from contextlib import asynccontextmanager
    from app.workers import live_worker

    event = asyncio.Event()
    messages = asyncio.Queue()

    async def iterate():
        while True:
            yield await messages.get()

    class Provider:
        token_seconds_remaining = 3600

        async def records(self, topic, **filters):
            return [SESSION] if topic == "sessions" else []

        @asynccontextmanager
        async def stream(self):
            yield SimpleNamespace(messages=iterate())

        def decode(self, message):
            return message

    async def capture(redis, state, owner, payload):
        if payload:
            assert state.state["status"] == "live"
            event.set()

    monkeypatch.setattr(live_worker, "utc_now", lambda: NOW)
    monkeypatch.setattr(live_worker, "publish", capture)
    collector = LiveCollector(None, Provider(), "test")
    task = asyncio.create_task(collector.collect())
    try:
        await messages.put(
            (
                "position",
                {"session_key": 1234, "driver_number": 63, "position": 1},
            )
        )
        await asyncio.wait_for(event.wait(), timeout=0.5)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_redis_publish_is_fenced_and_persists_snapshot_and_broadcast(
    monkeypatch,
):
    redis = FakeRedis(decode_responses=True)
    prefix = f"racepulse:test:live:{uuid4().hex}"
    keys = [prefix + suffix for suffix in (":lease", ":snapshot", ":channel")]
    for name, key in zip(("LEASE_KEY", "SNAPSHOT_KEY", "CHANNEL"), keys):
        monkeypatch.setattr(live, name, key)
    try:
        state = live.LiveTimingState(SESSION)
        pubsub = redis.pubsub()
        await pubsub.subscribe(keys[2])
        await pubsub.get_message()
        await redis.set(keys[0], "new-worker", ex=30)
        with pytest.raises(RuntimeError, match="lease lost"):
            await live.publish(redis, state, "old-worker")
        assert not await redis.exists(keys[1])
        await live.publish(redis, state, "new-worker")
        cached = json.loads(await redis.get(keys[1]))
        assert cached["session"]["session_key"] == 1234
        assert cached["sequence"] == 2
        assert (
            json.loads((await pubsub.get_message())["data"])["sequence"] == 2
        )
        await pubsub.aclose()
    finally:
        await redis.delete(*keys)
        await redis.aclose()


def test_websocket_sends_current_snapshot_and_retains_exact_provider_session(
    api_client, monkeypatch
):
    from app.main import app
    from app.api.v1 import live as live_api

    state = live.LiveTimingState(SESSION)
    state.apply(
        "drivers",
        {"session_key": 1234, "driver_number": 63, "full_name": "Test Driver"},
    )
    state.apply(
        "position", {"session_key": 1234, "driver_number": 63, "position": 2}
    )
    snapshot = state.snapshot()
    monkeypatch.setattr(settings, "openf1_live_enabled", True)
    monkeypatch.setattr(settings, "openf1_username", "test-account")
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "openf1_password", SecretStr("test-only"))

    class Subscription:
        async def subscribe(self, channel):
            assert channel == live.CHANNEL

        async def get_message(self, **kwargs):
            await asyncio.sleep(0.01)
            return None

        async def aclose(self):
            pass

    class Redis(Cache):
        def pubsub(self):
            return Subscription()

    monkeypatch.setattr(app.state, "redis", Redis(snapshot))
    with api_client.websocket_connect("/api/v1/live/stream") as socket:
        event = socket.receive_json()
        assert event["type"] == "snapshot"
        assert event["data"]["session"]["session_key"] == 1234
        assert event["data"]["drivers"][0]["position"] == 2
    assert live_api.router is not None
