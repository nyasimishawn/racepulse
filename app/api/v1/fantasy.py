from typing import NoReturn
from uuid import UUID

from pydantic import BaseModel
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from redis.asyncio import Redis
from sqlalchemy.orm import Session

from app.core.redis import (
    RedisUnavailableError,
    ensure_redis_available,
)
from app.core.rate_limit import (
    expensive_request_rate_limit,
    registration_rate_limit,
)
from app.core.config import settings
from app.core.fantasy_guest import create_guest, get_fantasy_fan
from app.core.security import (
    AuthenticatedUser,
    authenticate_access_token,
    ensure_active_profile,
    require_roles,
)
from app.db.database import SessionLocal, get_db
from app.models.durable_job import DurableJobType
from app.schemas.durable_job import DurableJobResponse
from app.schemas.fantasy import (
    FantasyCommunityResponse,
    FantasyDashboardResponse,
    FantasyEntryProgressResponse,
    FantasyFinalizeResponse,
    FantasyGroupCreateRequest,
    FantasyGroupDetailResponse,
    FantasyGroupInviteCodeResponse,
    FantasyGroupJoinRequest,
    FantasyGroupPodiumResponse,
    FantasyGroupSummaryResponse,
    FantasyLeaderboardResponse,
    FantasyPredictionResponse,
    FantasyPredictionUpdateRequest,
    FantasyQuestionResolutionRequest,
    FantasyQuestionResolutionResponse,
    FantasyQuestionSaveRequest,
    FantasyQuestionSaveResponse,
    FantasyRaceSummaryResponse,
    FantasyScoreRunResponse,
)
from app.services.fantasy_event_service import (
    publish_fantasy_event,
)
from app.services.job_dispatch_service import enqueue_job
from app.services.fantasy_service import (
    FantasyCommunityPrivacyError,
    FantasyError,
    FantasyGroupFullError,
    FantasyGroupNotFoundError,
    FantasyGroupPermissionError,
    FantasyGroupWeekendNotFinalizedError,
    FantasyInvalidRequestError,
    FantasyNotRaceSessionError,
    FantasyPredictionLockedError,
    FantasyRaceNotFoundError,
    FantasyResolutionStateError,
    FantasyService,
)
from app.services.fantasy_replay_service import ReplayError
from app.services.fantasy_replay_generic_service import (
    GenericFantasyReplayService as FantasyReplayService,
)
from app.services.user_profile_service import UserProfileService
from app.websocket.fantasy_streamer import FantasyStreamer


router = APIRouter(prefix="/fantasy", tags=["Fantasy"])


class FantasyGuestSessionResponse(BaseModel):
    guest_key: str


class FantasyReplayControlRequest(BaseModel):
    playing: bool
    speed: int


class FantasyReplayStartRequest(BaseModel):
    race_session_id: UUID


def _replay_error(error: ReplayError) -> NoReturn:
    message = str(error)
    raise HTTPException(
        status_code=(
            status.HTTP_404_NOT_FOUND
            if "not found" in message or "development only" in message
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        ),
        detail=message,
    ) from error


def _fantasy_for_race(
    db: Session, race_session_id: UUID, profile_id: UUID
) -> FantasyService:
    try:
        clock = FantasyReplayService(db).clock_for_race(
            race_session_id, profile_id
        )
    except ReplayError as error:
        _replay_error(error)
    return FantasyService(db, now=clock)


@router.get("/replay", summary="Get my current development replay")
def get_current_fantasy_replay(
    current_user: AuthenticatedUser = Depends(get_fantasy_fan),
    db: Session = Depends(get_db),
) -> dict | None:
    try:
        return FantasyReplayService(db).current(_profile_id(db, current_user))
    except ReplayError as error:
        _replay_error(error)


@router.post("/replay", summary="Start an imported weekend replay")
def start_fantasy_replay(
    payload: FantasyReplayStartRequest | None = None,
    current_user: AuthenticatedUser = Depends(get_fantasy_fan),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return FantasyReplayService(db).create(
            _profile_id(db, current_user),
            source_race_id=payload.race_session_id if payload else None,
        )
    except ReplayError as error:
        _replay_error(error)


@router.get("/replay/{race_session_id}", summary="Read replay clock and events")
def get_fantasy_replay(
    race_session_id: UUID,
    current_user: AuthenticatedUser = Depends(get_fantasy_fan),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return FantasyReplayService(db).state(
            race_session_id, _profile_id(db, current_user)
        )
    except ReplayError as error:
        _replay_error(error)


@router.put("/replay/{race_session_id}", summary="Play or pause replay")
def control_fantasy_replay(
    race_session_id: UUID,
    payload: FantasyReplayControlRequest,
    current_user: AuthenticatedUser = Depends(get_fantasy_fan),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return FantasyReplayService(db).control(
            race_session_id,
            _profile_id(db, current_user),
            playing=payload.playing,
            speed=payload.speed,
        )
    except ReplayError as error:
        _replay_error(error)


@router.post(
    "/guest-session",
    response_model=FantasyGuestSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a temporary Fantasy guest session for development",
)
def create_fantasy_guest_session(
    _: None = Depends(registration_rate_limit),
    db: Session = Depends(get_db),
) -> FantasyGuestSessionResponse:
    return FantasyGuestSessionResponse(guest_key=create_guest(db))


@router.get(
    "/races",
    response_model=list[FantasyRaceSummaryResponse],
    summary="List race weekends available for Fantasy",
)
def list_fantasy_races(
    year: int | None = Query(default=None, ge=1950, le=2100),
    current_user: AuthenticatedUser = Depends(get_fantasy_fan),
    db: Session = Depends(get_db),
) -> list[FantasyRaceSummaryResponse]:
    del current_user
    return FantasyService(db).list_races(year)


@router.get(
    "/me/dashboard",
    response_model=FantasyDashboardResponse,
    summary="Get the current fan's Fantasy dashboard",
)
def get_fantasy_dashboard(
    year: int | None = Query(default=None, ge=1950, le=2100),
    current_user: AuthenticatedUser = Depends(get_fantasy_fan),
    db: Session = Depends(get_db),
) -> FantasyDashboardResponse:
    profile_id = _profile_id(db, current_user)
    return FantasyService(db).get_dashboard(
        user_profile_id=profile_id,
        year=year,
    )


@router.get(
    "/races/{race_session_id}/prediction",
    response_model=FantasyPredictionResponse,
    summary="Get the current user's Fantasy prediction bundle",
)
def get_prediction(
    race_session_id: UUID,
    current_user: AuthenticatedUser = Depends(get_fantasy_fan),
    db: Session = Depends(get_db),
) -> FantasyPredictionResponse:
    try:
        profile_id = _profile_id(db, current_user)
        return _fantasy_for_race(db, race_session_id, profile_id).get_prediction(
            profile_id,
            race_session_id,
        )
    except FantasyError as error:
        raise _fantasy_http_error(error) from error


@router.get(
    "/races/{race_session_id}/entry",
    response_model=FantasyEntryProgressResponse,
    summary="Get the current user's Fantasy weekend entry progress",
)
def get_entry_progress(
    race_session_id: UUID,
    current_user: AuthenticatedUser = Depends(get_fantasy_fan),
    db: Session = Depends(get_db),
) -> FantasyEntryProgressResponse:
    try:
        profile_id = _profile_id(db, current_user)
        return _fantasy_for_race(db, race_session_id, profile_id).get_entry_progress(
            profile_id,
            race_session_id,
        )
    except FantasyError as error:
        raise _fantasy_http_error(error) from error


@router.put(
    "/races/{race_session_id}/prediction",
    response_model=FantasyPredictionResponse,
    summary="Create or update the current user's Fantasy picks",
)
def save_prediction(
    race_session_id: UUID,
    payload: FantasyPredictionUpdateRequest,
    current_user: AuthenticatedUser = Depends(get_fantasy_fan),
    db: Session = Depends(get_db),
) -> FantasyPredictionResponse:
    try:
        profile_id = _profile_id(db, current_user)
        return _fantasy_for_race(db, race_session_id, profile_id).save_prediction(
            profile_id,
            race_session_id,
            payload,
        )
    except FantasyError as error:
        raise _fantasy_http_error(error) from error


@router.put(
    "/races/{race_session_id}/questions/{question_key}",
    response_model=FantasyQuestionSaveResponse,
    summary="Save or clear one Fantasy question without changing others",
)
def save_question(
    race_session_id: UUID,
    question_key: str,
    payload: FantasyQuestionSaveRequest,
    current_user: AuthenticatedUser = Depends(get_fantasy_fan),
    db: Session = Depends(get_db),
) -> FantasyQuestionSaveResponse:
    try:
        profile_id = _profile_id(db, current_user)
        return _fantasy_for_race(db, race_session_id, profile_id).save_question(
            user_profile_id=profile_id,
            race_session_id=race_session_id,
            question_key=question_key,
            payload=payload,
        )
    except FantasyError as error:
        raise _fantasy_http_error(error) from error


@router.get(
    "/races/{race_session_id}/questions/{question_key}/community",
    response_model=FantasyCommunityResponse,
    summary="Show privacy-safe community pick percentages",
)
def get_community_percentages(
    race_session_id: UUID,
    question_key: str,
    current_user: AuthenticatedUser = Depends(get_fantasy_fan),
    db: Session = Depends(get_db),
) -> FantasyCommunityResponse:
    try:
        profile_id = _profile_id(db, current_user)
        return _fantasy_for_race(
            db, race_session_id, profile_id
        ).get_community_percentages(
            race_session_id=race_session_id,
            question_key=question_key,
            current_profile_id=profile_id,
        )
    except FantasyError as error:
        raise _fantasy_http_error(error) from error


@router.post(
    "/races/{race_session_id}/score",
    response_model=FantasyScoreRunResponse,
    summary="Score every currently verifiable Fantasy question",
)
def score_questions(
    race_session_id: UUID,
    background_tasks: BackgroundTasks,
    request: Request,
    _: None = Depends(expensive_request_rate_limit),
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> FantasyScoreRunResponse:
    del current_user

    try:
        response = FantasyService(db).score_available_questions(
            race_session_id
        )
        _publish_event(
            background_tasks,
            request,
            race_session_id,
            "fantasy.questions_scored",
            {
                "resolved_question_keys": (response.resolved_question_keys),
                "pending_question_keys": (response.pending_question_keys),
            },
        )
        return response
    except FantasyError as error:
        raise _fantasy_http_error(error) from error


@router.post(
    "/races/{race_session_id}/scoring-jobs",
    response_model=DurableJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue idempotent Fantasy scoring",
)
def queue_fantasy_scoring(
    race_session_id: UUID,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        max_length=255,
    ),
    _: None = Depends(expensive_request_rate_limit),
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> DurableJobResponse:
    del current_user
    return enqueue_job(
        db,
        job_type=DurableJobType.FANTASY_SCORE,
        target_id=race_session_id,
        idempotency_key=(
            f"fantasy-score:{idempotency_key}" if idempotency_key else None
        ),
        payload={"race_session_id": str(race_session_id)},
    )


@router.put(
    "/races/{race_session_id}/questions/{question_key}/resolution",
    response_model=FantasyQuestionResolutionResponse,
    summary="Set an editor-verified Fantasy outcome",
)
def set_question_resolution(
    race_session_id: UUID,
    question_key: str,
    payload: FantasyQuestionResolutionRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> FantasyQuestionResolutionResponse:
    try:
        profile_id = _profile_id(db, current_user)
        response = FantasyService(db).set_question_resolution(
            race_session_id=race_session_id,
            question_key=question_key,
            payload=payload,
            resolved_by_profile_id=profile_id,
        )
        _publish_event(
            background_tasks,
            request,
            race_session_id,
            "fantasy.question_resolved",
            {
                "question_key": response.question_key,
                "status": response.status,
            },
        )
        return response
    except FantasyError as error:
        raise _fantasy_http_error(error) from error


@router.post(
    "/races/{race_session_id}/finalize",
    response_model=FantasyFinalizeResponse,
    summary="Finalize group rankings for a race weekend",
)
def finalize_weekend(
    race_session_id: UUID,
    background_tasks: BackgroundTasks,
    request: Request,
    _: None = Depends(expensive_request_rate_limit),
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> FantasyFinalizeResponse:
    del current_user

    try:
        response = FantasyService(db).finalize_weekend(race_session_id)
        _publish_event(
            background_tasks,
            request,
            race_session_id,
            "fantasy.weekend_finalized",
            {
                "finalized_group_count": (response.finalized_group_count),
            },
        )
        return response
    except FantasyError as error:
        raise _fantasy_http_error(error) from error


@router.post(
    "/races/{race_session_id}/finalization-jobs",
    response_model=DurableJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue idempotent Fantasy weekend finalization",
)
def queue_fantasy_finalization(
    race_session_id: UUID,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        max_length=255,
    ),
    _: None = Depends(expensive_request_rate_limit),
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> DurableJobResponse:
    del current_user
    return enqueue_job(
        db,
        job_type=DurableJobType.FANTASY_FINALIZE,
        target_id=race_session_id,
        idempotency_key=(
            f"fantasy-finalize:{idempotency_key}" if idempotency_key else None
        ),
        payload={"race_session_id": str(race_session_id)},
    )


@router.get(
    "/leaderboards/global",
    response_model=FantasyLeaderboardResponse,
    summary="Get the international Fantasy leaderboard",
)
def get_global_leaderboard(
    year: int | None = Query(default=None, ge=1950, le=2100),
    race_session_id: UUID | None = None,
    current_user: AuthenticatedUser = Depends(get_fantasy_fan),
    db: Session = Depends(get_db),
) -> FantasyLeaderboardResponse:
    try:
        profile_id = _profile_id(db, current_user)
        service = (
            _fantasy_for_race(db, race_session_id, profile_id)
            if race_session_id is not None
            else FantasyService(db)
        )
        return service.get_global_leaderboard(
            current_profile_id=profile_id,
            year=year,
            race_session_id=race_session_id,
        )
    except FantasyError as error:
        raise _fantasy_http_error(error) from error


@router.post(
    "/groups",
    response_model=FantasyGroupDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a private Fantasy group",
)
def create_group(
    payload: FantasyGroupCreateRequest,
    current_user: AuthenticatedUser = Depends(get_fantasy_fan),
    db: Session = Depends(get_db),
) -> FantasyGroupDetailResponse:
    profile_id = _profile_id(db, current_user)
    return FantasyService(db).create_group(profile_id, payload)


@router.get(
    "/groups",
    response_model=list[FantasyGroupSummaryResponse],
    summary="List the current user's Fantasy groups",
)
def list_groups(
    current_user: AuthenticatedUser = Depends(get_fantasy_fan),
    db: Session = Depends(get_db),
) -> list[FantasyGroupSummaryResponse]:
    profile_id = _profile_id(db, current_user)
    return FantasyService(db).list_groups(profile_id)


@router.post(
    "/groups/join",
    response_model=FantasyGroupDetailResponse,
    summary="Join a private Fantasy group using an invite code",
)
def join_group(
    payload: FantasyGroupJoinRequest,
    current_user: AuthenticatedUser = Depends(get_fantasy_fan),
    db: Session = Depends(get_db),
) -> FantasyGroupDetailResponse:
    try:
        profile_id = _profile_id(db, current_user)
        return FantasyService(db).join_group(profile_id, payload)
    except FantasyError as error:
        raise _fantasy_http_error(error) from error


@router.get(
    "/groups/{group_id}",
    response_model=FantasyGroupDetailResponse,
    summary="Get a private Fantasy group",
)
def get_group(
    group_id: UUID,
    current_user: AuthenticatedUser = Depends(get_fantasy_fan),
    db: Session = Depends(get_db),
) -> FantasyGroupDetailResponse:
    try:
        profile_id = _profile_id(db, current_user)
        return FantasyService(db).get_group_detail(
            group_id,
            profile_id,
        )
    except FantasyError as error:
        raise _fantasy_http_error(error) from error


@router.post(
    "/groups/{group_id}/invite-code",
    response_model=FantasyGroupInviteCodeResponse,
    summary="Rotate a private Fantasy group invite code",
)
def rotate_group_invite_code(
    group_id: UUID,
    current_user: AuthenticatedUser = Depends(get_fantasy_fan),
    db: Session = Depends(get_db),
) -> FantasyGroupInviteCodeResponse:
    try:
        profile_id = _profile_id(db, current_user)
        return FantasyService(db).rotate_group_invite_code(
            group_id,
            profile_id,
        )
    except FantasyError as error:
        raise _fantasy_http_error(error) from error


@router.delete(
    "/groups/{group_id}/members/{member_profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a member from a private Fantasy group",
)
def remove_group_member(
    group_id: UUID,
    member_profile_id: UUID,
    current_user: AuthenticatedUser = Depends(get_fantasy_fan),
    db: Session = Depends(get_db),
) -> None:
    try:
        owner_profile_id = _profile_id(db, current_user)
        FantasyService(db).remove_group_member(
            group_id=group_id,
            owner_profile_id=owner_profile_id,
            member_profile_id=member_profile_id,
        )
    except FantasyError as error:
        raise _fantasy_http_error(error) from error


@router.get(
    "/groups/{group_id}/leaderboard",
    response_model=FantasyLeaderboardResponse,
    summary="Get a private Fantasy group leaderboard",
)
def get_group_leaderboard(
    group_id: UUID,
    year: int | None = Query(default=None, ge=1950, le=2100),
    race_session_id: UUID | None = None,
    current_user: AuthenticatedUser = Depends(get_fantasy_fan),
    db: Session = Depends(get_db),
) -> FantasyLeaderboardResponse:
    try:
        profile_id = _profile_id(db, current_user)
        return FantasyService(db).get_group_leaderboard(
            group_id=group_id,
            current_profile_id=profile_id,
            year=year,
            race_session_id=race_session_id,
        )
    except FantasyError as error:
        raise _fantasy_http_error(error) from error


@router.get(
    "/groups/{group_id}/weekends/{race_session_id}/podium",
    response_model=FantasyGroupPodiumResponse,
    summary="Get a private group's finalized race podium",
)
def get_group_podium(
    group_id: UUID,
    race_session_id: UUID,
    current_user: AuthenticatedUser = Depends(get_fantasy_fan),
    db: Session = Depends(get_db),
) -> FantasyGroupPodiumResponse:
    try:
        profile_id = _profile_id(db, current_user)
        return FantasyService(db).get_group_podium(
            group_id=group_id,
            race_session_id=race_session_id,
            profile_id=profile_id,
        )
    except FantasyError as error:
        raise _fantasy_http_error(error) from error


@router.websocket("/races/{race_session_id}/stream")
async def stream_fantasy_updates(
    websocket: WebSocket,
    race_session_id: UUID,
) -> None:
    from starlette.concurrency import run_in_threadpool

    if not await run_in_threadpool(_is_authorized_fantasy_stream, websocket):
        await websocket.close(code=1008)
        return

    await websocket.accept()

    try:
        redis: Redis | None = getattr(
            websocket.app.state,
            "redis",
            None,
        )

        if redis is None:
            raise RedisUnavailableError(
                "Redis client has not been configured."
            )

        await ensure_redis_available(redis)

        await FantasyStreamer().stream(
            websocket=websocket,
            redis=redis,
            race_session_id=race_session_id,
        )

    except RedisUnavailableError as error:
        await _send_stream_error(
            websocket,
            "REDIS_UNAVAILABLE",
            str(error),
        )
    except WebSocketDisconnect:
        return


def _is_authorized_fantasy_stream(websocket: WebSocket) -> bool:
    authorization = websocket.headers.get("authorization")
    if not authorization and settings.fantasy_guest_access_enabled:
        return True
    scheme, _, access_token = (authorization or "").partition(" ")

    if scheme.casefold() != "bearer" or not access_token.strip():
        return False

    db = SessionLocal()
    try:
        user = authenticate_access_token(access_token.strip())
        ensure_active_profile(user, db)
        UserProfileService(db).get_or_create_profile(user)
        return user.has_role("fan")
    except HTTPException:
        return False
    finally:
        db.close()


def _profile_id(
    db: Session,
    current_user: AuthenticatedUser,
) -> UUID:
    return (
        UserProfileService(db).get_or_create_profile(current_user).profile_id
    )


def _publish_event(
    background_tasks: BackgroundTasks,
    request: Request,
    race_session_id: UUID,
    event_type: str,
    payload: dict[str, object],
) -> None:
    redis = getattr(request.app.state, "redis", None)

    background_tasks.add_task(
        publish_fantasy_event,
        redis,
        race_session_id=race_session_id,
        event_type=event_type,
        payload=payload,
    )


async def _send_stream_error(
    websocket: WebSocket,
    code: str,
    message: str,
) -> None:
    await websocket.send_json(
        {
            "type": "fantasy.error",
            "code": code,
            "message": message,
        }
    )
    await websocket.close(code=1011)


def _fantasy_http_error(error: FantasyError) -> NoReturn:
    if isinstance(
        error,
        (
            FantasyRaceNotFoundError,
            FantasyGroupNotFoundError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        )

    if isinstance(
        error,
        (
            FantasyGroupPermissionError,
            FantasyCommunityPrivacyError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(error),
        )

    if isinstance(
        error,
        (
            FantasyPredictionLockedError,
            FantasyGroupFullError,
            FantasyResolutionStateError,
            FantasyGroupWeekendNotFinalizedError,
        ),
    ):
        detail: dict[str, object] = {"message": str(error)}

        if isinstance(error, FantasyPredictionLockedError):
            detail["question_keys"] = error.question_keys

        if isinstance(error, FantasyResolutionStateError):
            detail["pending_question_keys"] = error.pending_question_keys

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail,
        )

    if isinstance(
        error,
        (
            FantasyInvalidRequestError,
            FantasyNotRaceSessionError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        )

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Fantasy operation failed unexpectedly.",
    )
