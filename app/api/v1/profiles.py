from typing import NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.security import AuthenticatedUser, require_roles
from app.db.database import get_db
from app.schemas.profiles import (
    DriverHistoryResponse,
    DriverProfileResponse,
    DriverProfileUpsertRequest,
    DriverSummaryResponse,
    NotableMomentResponse,
    ProfileNotableMomentCreateRequest,
    ProfileNotableMomentUpdateRequest,
    TeamHistoryResponse,
    TeamProfileResponse,
    TeamProfileUpsertRequest,
    TeamSummaryResponse,
)
from app.services.profile_content_service import (
    DriverNotFoundError,
    NotableMomentNotFoundError,
    ProfileContentError,
    ProfileContentService,
    ProfileContentValidationError,
    TeamNotFoundError,
)
from app.services.user_profile_service import UserProfileService


router = APIRouter(tags=["Profiles"])


@router.get(
    "/drivers",
    response_model=list[DriverSummaryResponse],
    summary="List imported drivers for public profile navigation",
)
def list_drivers(
    query: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=100, ge=1, le=250),
    db: Session = Depends(get_db),
) -> list[DriverSummaryResponse]:
    return ProfileContentService(db).list_drivers(
        query=query,
        limit=limit,
    )


@router.get(
    "/drivers/{driver_id}/history",
    response_model=DriverHistoryResponse,
    summary="Get derived imported race history and explicit coverage",
)
def get_driver_history(
    driver_id: UUID,
    year: int | None = Query(default=None, ge=1950, le=2100),
    db: Session = Depends(get_db),
) -> DriverHistoryResponse:
    try:
        return ProfileContentService(db).get_driver_history(
            driver_id=driver_id,
            year=year,
        )
    except ProfileContentError as error:
        _profile_http_error(error)


@router.get(
    "/drivers/{driver_id}",
    response_model=DriverProfileResponse,
    summary="Get a driver profile and sourced notable moments",
)
def get_driver_profile(
    driver_id: UUID,
    db: Session = Depends(get_db),
) -> DriverProfileResponse:
    try:
        return ProfileContentService(db).get_driver_profile(driver_id)
    except ProfileContentError as error:
        _profile_http_error(error)


@router.put(
    "/drivers/{driver_id}/profile",
    response_model=DriverProfileResponse,
    summary="Create or replace a sourced driver biography",
)
def upsert_driver_profile(
    driver_id: UUID,
    payload: DriverProfileUpsertRequest,
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> DriverProfileResponse:
    try:
        return ProfileContentService(db).upsert_driver_profile(
            driver_id=driver_id,
            payload=payload,
            editor_profile_id=_profile_id(db, current_user),
        )
    except ProfileContentError as error:
        _profile_http_error(error)


@router.post(
    "/drivers/{driver_id}/notable-moments",
    response_model=NotableMomentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a sourced notable moment to a driver profile",
)
def create_driver_notable_moment(
    driver_id: UUID,
    payload: ProfileNotableMomentCreateRequest,
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> NotableMomentResponse:
    try:
        return ProfileContentService(db).create_driver_notable_moment(
            driver_id=driver_id,
            payload=payload,
            editor_profile_id=_profile_id(db, current_user),
        )
    except ProfileContentError as error:
        _profile_http_error(error)


@router.patch(
    "/drivers/{driver_id}/notable-moments/{moment_id}",
    response_model=NotableMomentResponse,
    summary="Update a sourced driver notable moment",
)
def update_driver_notable_moment(
    driver_id: UUID,
    moment_id: UUID,
    payload: ProfileNotableMomentUpdateRequest,
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> NotableMomentResponse:
    try:
        return ProfileContentService(db).update_driver_notable_moment(
            driver_id=driver_id,
            moment_id=moment_id,
            payload=payload,
            editor_profile_id=_profile_id(db, current_user),
        )
    except ProfileContentError as error:
        _profile_http_error(error)


@router.delete(
    "/drivers/{driver_id}/notable-moments/{moment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a driver notable moment",
)
def delete_driver_notable_moment(
    driver_id: UUID,
    moment_id: UUID,
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> None:
    del current_user
    try:
        ProfileContentService(db).delete_driver_notable_moment(
            driver_id=driver_id,
            moment_id=moment_id,
        )
    except ProfileContentError as error:
        _profile_http_error(error)


@router.get(
    "/teams",
    response_model=list[TeamSummaryResponse],
    summary="List imported teams for public profile navigation",
)
def list_teams(
    query: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=100, ge=1, le=250),
    db: Session = Depends(get_db),
) -> list[TeamSummaryResponse]:
    return ProfileContentService(db).list_teams(
        query=query,
        limit=limit,
    )


@router.get(
    "/teams/{team_id}/history",
    response_model=TeamHistoryResponse,
    summary="Get derived imported team race history and coverage",
)
def get_team_history(
    team_id: UUID,
    year: int | None = Query(default=None, ge=1950, le=2100),
    db: Session = Depends(get_db),
) -> TeamHistoryResponse:
    try:
        return ProfileContentService(db).get_team_history(
            team_id=team_id,
            year=year,
        )
    except ProfileContentError as error:
        _profile_http_error(error)


@router.get(
    "/teams/{team_id}",
    response_model=TeamProfileResponse,
    summary="Get a team profile and sourced notable moments",
)
def get_team_profile(
    team_id: UUID,
    db: Session = Depends(get_db),
) -> TeamProfileResponse:
    try:
        return ProfileContentService(db).get_team_profile(team_id)
    except ProfileContentError as error:
        _profile_http_error(error)


@router.put(
    "/teams/{team_id}/profile",
    response_model=TeamProfileResponse,
    summary="Create or replace a sourced team biography",
)
def upsert_team_profile(
    team_id: UUID,
    payload: TeamProfileUpsertRequest,
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> TeamProfileResponse:
    try:
        return ProfileContentService(db).upsert_team_profile(
            team_id=team_id,
            payload=payload,
            editor_profile_id=_profile_id(db, current_user),
        )
    except ProfileContentError as error:
        _profile_http_error(error)


@router.post(
    "/teams/{team_id}/notable-moments",
    response_model=NotableMomentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a sourced notable moment to a team profile",
)
def create_team_notable_moment(
    team_id: UUID,
    payload: ProfileNotableMomentCreateRequest,
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> NotableMomentResponse:
    try:
        return ProfileContentService(db).create_team_notable_moment(
            team_id=team_id,
            payload=payload,
            editor_profile_id=_profile_id(db, current_user),
        )
    except ProfileContentError as error:
        _profile_http_error(error)


@router.patch(
    "/teams/{team_id}/notable-moments/{moment_id}",
    response_model=NotableMomentResponse,
    summary="Update a sourced team notable moment",
)
def update_team_notable_moment(
    team_id: UUID,
    moment_id: UUID,
    payload: ProfileNotableMomentUpdateRequest,
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> NotableMomentResponse:
    try:
        return ProfileContentService(db).update_team_notable_moment(
            team_id=team_id,
            moment_id=moment_id,
            payload=payload,
            editor_profile_id=_profile_id(db, current_user),
        )
    except ProfileContentError as error:
        _profile_http_error(error)


@router.delete(
    "/teams/{team_id}/notable-moments/{moment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a team notable moment",
)
def delete_team_notable_moment(
    team_id: UUID,
    moment_id: UUID,
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> None:
    del current_user
    try:
        ProfileContentService(db).delete_team_notable_moment(
            team_id=team_id,
            moment_id=moment_id,
        )
    except ProfileContentError as error:
        _profile_http_error(error)


def _profile_id(db: Session, current_user: AuthenticatedUser) -> UUID:
    return UserProfileService(db).get_or_create_profile(current_user).profile_id


def _profile_http_error(error: ProfileContentError) -> NoReturn:
    if isinstance(
        error,
        (
            DriverNotFoundError,
            TeamNotFoundError,
            NotableMomentNotFoundError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    if isinstance(error, ProfileContentValidationError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Profile operation failed unexpectedly.",
    ) from error
