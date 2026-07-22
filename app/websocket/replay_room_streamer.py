import asyncio
from uuid import UUID

from fastapi import WebSocket

from app.schemas.replay_room import (
    ReplayRoomCompletedMessage,
    ReplayRoomReadyMessage,
    ReplayRoomTickMessage,
)
from app.services.replay_room_map_service import (
    ReplayRoomMapService,
)
from app.services.replay_room_service import ReplayRoomService


class ReplayRoomStreamer:
    async def stream(
        self,
        *,
        websocket: WebSocket,
        service: ReplayRoomService,
        room_id: UUID,
        interval_ms: int,
    ) -> None:
        map_service = ReplayRoomMapService(service.redis)

        room = await service.get_room(room_id)
        snapshot = await service.get_snapshot(room_id)

        map_frame = await self._get_map_frame(
            map_service=map_service,
            room=room,
            source_cursor_ms=snapshot.source_cursor_ms,
        )

        initial_context_events = (
            await service.get_context_events_between(
                room_id,
                after_cursor_ms=(
                    snapshot.source_cursor_ms - 1
                ),
                through_cursor_ms=snapshot.source_cursor_ms,
            )
        )

        await websocket.send_json(
            ReplayRoomReadyMessage(
                room=room,
                snapshot=snapshot,
                map_frame=map_frame,
                initial_context_events=initial_context_events,
            ).model_dump(mode="json")
        )

        last_cursor_ms = snapshot.source_cursor_ms
        last_cursor_epoch = snapshot.cursor_epoch

        last_signature = self._signature(snapshot)

        while True:
            room = await service.get_room(room_id)
            snapshot = await service.get_snapshot(room_id)

            reset_detected = (
                snapshot.cursor_epoch != last_cursor_epoch
                or snapshot.source_cursor_ms < last_cursor_ms
            )

            if reset_detected:
                context_events = []
                window_start_exclusive_ms = (
                    snapshot.source_cursor_ms
                )
            else:
                window_start_exclusive_ms = last_cursor_ms

                context_events = (
                    await service.get_context_events_between(
                        room_id,
                        after_cursor_ms=last_cursor_ms,
                        through_cursor_ms=(
                            snapshot.source_cursor_ms
                        ),
                    )
                )

            signature = self._signature(snapshot)

            if signature != last_signature:
                map_frame = await self._get_map_frame(
                    map_service=map_service,
                    room=room,
                    source_cursor_ms=snapshot.source_cursor_ms,
                )

                await websocket.send_json(
                    ReplayRoomTickMessage(
                        room_id=room_id,
                        snapshot=snapshot,
                        map_frame=map_frame,
                        context_events=context_events,
                        event_window_start_exclusive_ms=(
                            window_start_exclusive_ms
                        ),
                        event_window_end_inclusive_ms=(
                            snapshot.source_cursor_ms
                        ),
                    ).model_dump(mode="json")
                )

                last_signature = signature

            last_cursor_ms = snapshot.source_cursor_ms
            last_cursor_epoch = snapshot.cursor_epoch

            if snapshot.status == "COMPLETED":
                await websocket.send_json(
                    ReplayRoomCompletedMessage(
                        snapshot=snapshot,
                        map_frame=map_frame,
                    ).model_dump(mode="json")
                )

                await websocket.close(code=1000)
                return

            await asyncio.sleep(interval_ms / 1000)

    @staticmethod
    async def _get_map_frame(
        *,
        map_service: ReplayRoomMapService,
        room,
        source_cursor_ms: int,
    ):
        try:
            return await map_service.get_frame(
                room=room,
                source_cursor_ms=source_cursor_ms,
            )
        except Exception:
            # Map failure must never stop the timing replay.
            return None

    @staticmethod
    def _signature(snapshot) -> tuple[object, ...]:
        return (
            snapshot.status,
            snapshot.revision,
            snapshot.cursor_epoch,
            snapshot.source_cursor_ms,
            snapshot.applied_event_count,
            snapshot.context_state.passed_event_count,
        )