import asyncio

from fastapi import WebSocket

from app.schemas.replay_room import (
    ReplayRoomCompletedMessage,
    ReplayRoomReadyMessage,
    ReplayRoomSnapshotMessage,
)
from app.services.replay_room_service import ReplayRoomService


class ReplayRoomStreamer:
    async def stream(
        self,
        *,
        websocket: WebSocket,
        service: ReplayRoomService,
        room_id,
        interval_ms: int,
    ) -> None:
        room = await service.get_room(room_id)
        snapshot = await service.get_snapshot(room_id)

        await websocket.send_json(
            ReplayRoomReadyMessage(
                room=room,
                snapshot=snapshot,
            ).model_dump(mode="json")
        )

        last_signature: tuple[object, ...] | None = None

        while True:
            snapshot = await service.get_snapshot(room_id)

            signature = (
                snapshot.status,
                snapshot.revision,
                snapshot.source_cursor_ms,
                snapshot.applied_event_count,
            )

            if signature != last_signature:
                await websocket.send_json(
                    ReplayRoomSnapshotMessage(
                        snapshot=snapshot
                    ).model_dump(mode="json")
                )

                last_signature = signature

            if snapshot.status == "COMPLETED":
                await websocket.send_json(
                    ReplayRoomCompletedMessage(
                        snapshot=snapshot
                    ).model_dump(mode="json")
                )

                await websocket.close(code=1000)
                return

            await asyncio.sleep(interval_ms / 1000)