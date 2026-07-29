from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.security import AuthenticatedUser, get_current_user
from app.db.database import get_db
from app.schemas.user import (
    CurrentUserProfileResponse,
    CurrentUserProfileUpdate,
)
from app.services.user_profile_service import UserProfileService


router = APIRouter(prefix="/users", tags=["Users"])


@router.get(
    "/me",
    response_model=CurrentUserProfileResponse,
    summary="Get the authenticated user's RacePulse profile",
)
def get_my_profile(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CurrentUserProfileResponse:
    return UserProfileService(db).get_or_create_profile(
        current_user
    )


@router.patch(
    "/me",
    response_model=CurrentUserProfileResponse,
    summary="Update the authenticated user's RacePulse profile",
)
def update_my_profile(
    payload: CurrentUserProfileUpdate,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CurrentUserProfileResponse:
    return UserProfileService(db).update_profile(
        current_user,
        payload,
    )