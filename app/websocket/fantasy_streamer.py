import asyncio
import json
from uuid import UUID

from fastapi import WebSocket
from redis.asyncio import Redis

from app.services.fantasy_event_service import (
    fantasy_event_channel,
)


class FantasyStreamer:
    async def stream(
        self,
        *,
        websocket: WebSocket,
        redis: Redis,
        race_session_id: UUID,
    ) -> None:
        pubsub = redis.pubsub()
        heartbeat_count = 0

        try:
            await pubsub.subscribe(
                fantasy_event_channel(race_session_id)
            )
            await websocket.send_json(
                {
                    "type": "fantasy.ready",
                    "race_session_id": str(race_session_id),
                }
            )

            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )

                if message and message.get("type") == "message":
                    payload = json.loads(message["data"])

                    if isinstance(payload, dict):
                        await websocket.send_json(payload)

                heartbeat_count += 1

                if heartbeat_count >= 25:
                    await websocket.send_json(
                        {
                            "type": "fantasy.heartbeat",
                            "race_session_id": str(race_session_id),
                        }
                    )
                    heartbeat_count = 0

                await asyncio.sleep(0.05)

        finally:
            await pubsub.unsubscribe(
                fantasy_event_channel(race_session_id)
            )
            await pubsub.aclose()