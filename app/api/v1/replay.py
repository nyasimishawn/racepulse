import logging
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlalchemy.orm import Session

from app.db.database import SessionLocal, get_db
from app.schemas.replay import (
    ReplayErrorMessage,
    ReplayManifestResponse,
)
from app.services.replay_service import (
    ReplayInvalidRequestError,
    ReplayService,
    ReplaySourceLapNotFoundError,
    ReplayTelemetryUnavailableError,
)
from app.websocket.replay_streamer import ReplayStreamer


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/replay",
    tags=["Replay"],
)


@router.get(
    "/sessions/{race_session_id}/drivers/{driver_number}/laps/"
    "{lap_number}/manifest",
    response_model=ReplayManifestResponse,
    summary="Get replay metadata for one imported driver lap",
)
def get_replay_manifest(
    race_session_id: UUID,
    driver_number: str,
    lap_number: int,
    playback_speed: float = Query(
        default=1.0,
        ge=0.25,
        le=10.0,
    ),
    db: Session = Depends(get_db),
) -> ReplayManifestResponse:
    try:
        return ReplayService(db).get_manifest(
            race_session_id=race_session_id,
            driver_number=driver_number,
            lap_number=lap_number,
            playback_speed=playback_speed,
        )

    except ReplaySourceLapNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except (
        ReplayTelemetryUnavailableError,
        ReplayInvalidRequestError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error


@router.websocket(
    "/sessions/{race_session_id}/drivers/{driver_number}/laps/"
    "{lap_number}/stream"
)
async def stream_lap_replay(
    websocket: WebSocket,
    race_session_id: UUID,
    driver_number: str,
    lap_number: int,
    playback_speed: float = Query(
        default=1.0,
        ge=0.25,
        le=10.0,
    ),
    from_ms: int = Query(default=0, ge=0),
) -> None:
    await websocket.accept()

    db = SessionLocal()

    try:
        replay_service = ReplayService(db)

        replay_service.validate_playback_speed(playback_speed)

        plan = replay_service.load_plan(
            race_session_id=race_session_id,
            driver_number=driver_number,
            lap_number=lap_number,
        )

        replay_service.validate_start_position(
            plan=plan,
            from_ms=from_ms,
        )

    except ReplaySourceLapNotFoundError as error:
        await _send_replay_error(
            websocket,
            code="SOURCE_LAP_NOT_FOUND",
            message=str(error),
        )
        return

    except ReplayTelemetryUnavailableError as error:
        await _send_replay_error(
            websocket,
            code="TELEMETRY_NOT_READY",
            message=str(error),
        )
        return

    except ReplayInvalidRequestError as error:
        await _send_replay_error(
            websocket,
            code="INVALID_REPLAY_REQUEST",
            message=str(error),
        )
        return

    finally:
        db.close()

    try:
        await ReplayStreamer().stream(
            websocket=websocket,
            plan=plan,
            playback_speed=playback_speed,
            from_ms=from_ms,
        )

    except WebSocketDisconnect:
        logger.info(
            "Replay client disconnected: session=%s driver=%s lap=%s",
            race_session_id,
            driver_number,
            lap_number,
        )


async def _send_replay_error(
    websocket: WebSocket,
    *,
    code: str,
    message: str,
) -> None:
    await websocket.send_json(
        ReplayErrorMessage(
            code=code,
            message=message,
        ).model_dump(mode="json")
    )

    await websocket.close(code=1008)