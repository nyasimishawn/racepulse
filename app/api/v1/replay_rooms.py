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
from redis.asyncio import Redis

from app.core.redis import (
    RedisUnavailableError,
    ensure_redis_available,
    get_redis,
)
from app.schemas.replay_room import (
    CreateReplayRoomRequest,
    ReplayRoomCommandRequest,
    ReplayRoomErrorMessage,
    ReplayRoomResponse,
    ReplayRoomSnapshotResponse,
)
from app.services.replay_room_service import (
    NonRaceReplaySessionError,
    ReplayRoomInvalidRequestError,
    ReplayRoomNotFoundError,
    ReplayRoomRevisionConflict,
    ReplayRoomService,
    ReplaySourceSessionNotFoundError,
)
from app.websocket.replay_room_streamer import ReplayRoomStreamer

router = APIRouter(
    prefix="/replay/rooms",
    tags=["Replay Rooms"],
)


@router.post(
    "",
    response_model=ReplayRoomResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a shared full-session timing replay room",
)
async def create_replay_room(
    request: CreateReplayRoomRequest,
    redis: Redis = Depends(get_redis),
) -> ReplayRoomResponse:
    try:
        return await ReplayRoomService(redis).create_room(request)

    except ReplaySourceSessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except (
        NonRaceReplaySessionError,
        ReplayRoomInvalidRequestError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error


@router.get(
    "/{room_id}",
    response_model=ReplayRoomResponse,
    summary="Get shared replay-room state",
)
async def get_replay_room(
    room_id: UUID,
    redis: Redis = Depends(get_redis),
) -> ReplayRoomResponse:
    try:
        return await ReplayRoomService(redis).get_room(room_id)

    except ReplayRoomNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error


@router.get(
    "/{room_id}/snapshot",
    response_model=ReplayRoomSnapshotResponse,
    summary="Get the current all-driver timing tower",
)
async def get_replay_room_snapshot(
    room_id: UUID,
    redis: Redis = Depends(get_redis),
) -> ReplayRoomSnapshotResponse:
    try:
        return await ReplayRoomService(redis).get_snapshot(room_id)

    except ReplayRoomNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error


@router.post(
    "/{room_id}/commands",
    response_model=ReplayRoomResponse,
    summary="Control a shared replay room",
)
async def command_replay_room(
    room_id: UUID,
    request: ReplayRoomCommandRequest,
    redis: Redis = Depends(get_redis),
) -> ReplayRoomResponse:
    try:
        return await ReplayRoomService(redis).command(
            room_id,
            request,
        )

    except ReplayRoomNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except ReplayRoomRevisionConflict as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": str(error),
                "actual_revision": error.actual_revision,
            },
        ) from error

    except ReplayRoomInvalidRequestError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error


@router.websocket("/{room_id}/stream")
async def stream_replay_room(
    websocket: WebSocket,
    room_id: UUID,
    interval_ms: int = Query(default=250, ge=100, le=1000),
) -> None:
    await websocket.accept()

    try:
        redis: Redis = websocket.app.state.redis
        await ensure_redis_available(redis)

        service = ReplayRoomService(redis)

        await ReplayRoomStreamer().stream(
            websocket=websocket,
            service=service,
            room_id=room_id,
            interval_ms=interval_ms,
        )

    except ReplayRoomNotFoundError as error:
        await _send_room_error(
            websocket,
            code="ROOM_NOT_FOUND",
            message=str(error),
        )

    except RedisUnavailableError as error:
        await _send_room_error(
            websocket,
            code="REDIS_UNAVAILABLE",
            message=str(error),
        )

    except WebSocketDisconnect:
        return


async def _send_room_error(
    websocket: WebSocket,
    *,
    code: str,
    message: str,
) -> None:
    await websocket.send_json(
        ReplayRoomErrorMessage(
            code=code,
            message=message,
        ).model_dump(mode="json")
    )

    await websocket.close(code=1008)