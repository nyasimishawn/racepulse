from typing import NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.security import AuthenticatedUser, require_roles
from app.db.database import get_db
from app.schemas.editorial import (
    EditorialPublicationStatus,
    EditorialUpdateCreateRequest,
    EditorialUpdateResponse,
    EditorialUpdateType,
    EditorialUpdateUpdateRequest,
)
from app.services.profile_content_service import (
    EditorialAssociationError,
    EditorialContentService,
    EditorialUpdateNotFoundError,
    ProfileContentError,
    ProfileContentValidationError,
)
from app.services.user_profile_service import UserProfileService


router = APIRouter(prefix="/editorial/updates", tags=["Editorial"])


@router.get(
    "",
    response_model=list[EditorialUpdateResponse],
    summary="List published FIA, team, Pirelli, and race editorial updates",
)
def list_editorial_updates(
    update_type: EditorialUpdateType | None = None,
    meeting_id: UUID | None = None,
    race_session_id: UUID | None = None,
    driver_id: UUID | None = None,
    team_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[EditorialUpdateResponse]:
    return EditorialContentService(db).list_public_updates(
        update_type=update_type,
        meeting_id=meeting_id,
        race_session_id=race_session_id,
        driver_id=driver_id,
        team_id=team_id,
        limit=limit,
    )


@router.get(
    "/manage",
    response_model=list[EditorialUpdateResponse],
    summary="List editorial updates, including drafts, for editors",
)
def list_editorial_updates_for_editor(
    update_type: EditorialUpdateType | None = None,
    publication_status: EditorialPublicationStatus | None = None,
    meeting_id: UUID | None = None,
    race_session_id: UUID | None = None,
    driver_id: UUID | None = None,
    team_id: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> list[EditorialUpdateResponse]:
    del current_user
    return EditorialContentService(db).list_editor_updates(
        update_type=update_type,
        publication_status=publication_status,
        meeting_id=meeting_id,
        race_session_id=race_session_id,
        driver_id=driver_id,
        team_id=team_id,
        limit=limit,
    )


@router.get(
    "/{update_id}",
    response_model=EditorialUpdateResponse,
    summary="Get one published editorial update",
)
def get_editorial_update(
    update_id: UUID,
    db: Session = Depends(get_db),
) -> EditorialUpdateResponse:
    try:
        return EditorialContentService(db).get_public_update(update_id)
    except ProfileContentError as error:
        _editorial_http_error(error)


@router.post(
    "",
    response_model=EditorialUpdateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a sourced editorial update",
)
def create_editorial_update(
    payload: EditorialUpdateCreateRequest,
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> EditorialUpdateResponse:
    try:
        return EditorialContentService(db).create_update(
            payload=payload,
            editor_profile_id=_profile_id(db, current_user),
        )
    except ProfileContentError as error:
        _editorial_http_error(error)


@router.patch(
    "/{update_id}",
    response_model=EditorialUpdateResponse,
    summary="Update or publish a sourced editorial update",
)
def update_editorial_update(
    update_id: UUID,
    payload: EditorialUpdateUpdateRequest,
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> EditorialUpdateResponse:
    try:
        return EditorialContentService(db).update_update(
            update_id=update_id,
            payload=payload,
            editor_profile_id=_profile_id(db, current_user),
        )
    except ProfileContentError as error:
        _editorial_http_error(error)


@router.delete(
    "/{update_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an editorial update",
)
def delete_editorial_update(
    update_id: UUID,
    current_user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
) -> None:
    del current_user
    try:
        EditorialContentService(db).delete_update(update_id)
    except ProfileContentError as error:
        _editorial_http_error(error)


def _profile_id(db: Session, current_user: AuthenticatedUser) -> UUID:
    return UserProfileService(db).get_or_create_profile(current_user).profile_id


def _editorial_http_error(error: ProfileContentError) -> NoReturn:
    if isinstance(error, EditorialUpdateNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    if isinstance(
        error,
        (EditorialAssociationError, ProfileContentValidationError),
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Editorial operation failed unexpectedly.",
    ) from error
