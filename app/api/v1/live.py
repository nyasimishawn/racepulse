import asyncio
from contextlib import suppress
import json
import re
import time

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from redis.exceptions import RedisError

from app.core.config import settings
from app.services.live_timing import CHANNEL, read_snapshot

router = APIRouter(prefix="/live", tags=["Live timing"])


@router.get("/current")
async def current(request: Request):
    # Pull-to-refresh reads the one shared snapshot, never opens a provider feed.
    return await read_snapshot(
        getattr(request.app.state, "redis", None),
        configured=settings.openf1_live_configured,
    )


@router.websocket("/stream")
async def stream(websocket: WebSocket):
    origin = websocket.headers.get("origin")
    if origin and not re.fullmatch(settings.cors_allowed_origin_regex, origin):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    redis = getattr(websocket.app.state, "redis", None)
    pubsub = None
    try:
        if redis and settings.openf1_live_configured:
            pubsub = redis.pubsub()
            # Subscribe before reading: queued updates cover the snapshot race.
            await pubsub.subscribe(CHANNEL)
        snapshot = await read_snapshot(
            redis, configured=settings.openf1_live_configured
        )
        await websocket.send_json({"type": "snapshot", "data": snapshot})
        if not pubsub:
            await websocket.close(code=1000)
            return
        epoch, sequence = snapshot["epoch"], snapshot["sequence"]
        heartbeat_at = time.monotonic()
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1
            )
            if message and message.get("type") == "message":
                update = json.loads(message["data"])
                if update["epoch"] != epoch or update["sequence"] > sequence:
                    # Re-read to apply current freshness/finished status.
                    snapshot = await read_snapshot(redis, configured=True)
                    epoch, sequence = snapshot["epoch"], snapshot["sequence"]
                    await websocket.send_json(
                        {"type": "snapshot", "data": snapshot}
                    )
                    heartbeat_at = time.monotonic()
            else:
                if time.monotonic() - heartbeat_at >= 15:
                    snapshot = await read_snapshot(redis, configured=True)
                    await websocket.send_json(
                        {"type": "snapshot", "data": snapshot}
                    )
                    heartbeat_at = time.monotonic()
                # A receive also detects disconnected idle clients promptly.
                try:
                    await asyncio.wait_for(
                        websocket.receive_text(), timeout=0.05
                    )
                except asyncio.TimeoutError:
                    pass
    except (WebSocketDisconnect, RuntimeError):
        pass
    except (RedisError, ValueError, KeyError):
        with suppress(RuntimeError, WebSocketDisconnect):
            await websocket.send_json(
                {
                    "type": "snapshot",
                    "data": await read_snapshot(None, configured=True),
                }
            )
            await websocket.close(code=1013)
    finally:
        if pubsub:
            await pubsub.aclose()
