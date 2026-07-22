import asyncio
import time

from fastapi import WebSocket

from app.schemas.replay import (
    ReplayCompletedMessage,
    ReplayReadyMessage,
    ReplayTelemetryDataResponse,
    ReplayTelemetryFrameMessage,
)
from app.services.replay_service import ReplayPlan


class ReplayStreamer:
    async def stream(
        self,
        *,
        websocket: WebSocket,
        plan: ReplayPlan,
        playback_speed: float,
        from_ms: int,
    ) -> None:
        await websocket.send_json(
            ReplayReadyMessage(
                replay_version="single-lap-replay-v1",
                source=plan.source,
                playback_speed=playback_speed,
                from_ms=from_ms,
                telemetry_frame_count=len(plan.frames),
            ).model_dump(mode="json")
        )

        replay_started_at = time.monotonic()
        frames_sent = 0

        for frame in plan.frames:
            if frame.elapsed_time_ms < from_ms:
                continue

            due_seconds = (
                (frame.elapsed_time_ms - from_ms)
                / 1000
                / playback_speed
            )

            delay_seconds = due_seconds - (
                time.monotonic() - replay_started_at
            )

            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)

            progress_percentage = round(
                frame.elapsed_time_ms
                / plan.source.duration_ms
                * 100,
                3,
            )

            await websocket.send_json(
                ReplayTelemetryFrameMessage(
                    sequence=frame.sequence,
                    elapsed_time_ms=frame.elapsed_time_ms,
                    source_relative_time_ms=(
                        frame.source_relative_time_ms
                    ),
                    duration_ms=plan.source.duration_ms,
                    progress_percentage=progress_percentage,
                    telemetry=ReplayTelemetryDataResponse(
                        speed_kph=frame.speed_kph,
                        throttle_percentage=(
                            frame.throttle_percentage
                        ),
                        brake_applied=frame.brake_applied,
                        rpm=frame.rpm,
                        gear=frame.gear,
                        drs=frame.drs,
                        x=frame.x,
                        y=frame.y,
                        z=frame.z,
                        distance_m=frame.distance_m,
                    ),
                ).model_dump(mode="json")
            )

            frames_sent += 1

        await websocket.send_json(
            ReplayCompletedMessage(
                duration_ms=plan.source.duration_ms,
                frames_sent=frames_sent,
            ).model_dump(mode="json")
        )

        await websocket.close(code=1000)