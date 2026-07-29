from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import AuthenticatedUser
from app.models.user_profile import UserProfile
from app.schemas.user import (
    CurrentUserProfileResponse,
    CurrentUserProfileUpdate,
)


class UserProfileService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_or_create_profile(
        self,
        current_user: AuthenticatedUser,
    ) -> CurrentUserProfileResponse:
        profile = self._synchronize_profile(current_user)
        self._commit(profile)

        return self._response(profile, current_user)

    def update_profile(
        self,
        current_user: AuthenticatedUser,
        payload: CurrentUserProfileUpdate,
    ) -> CurrentUserProfileResponse:
        profile = self._synchronize_profile(current_user)

        if "display_name" in payload.model_fields_set:
            profile.display_name = payload.display_name

        self._commit(profile)

        return self._response(profile, current_user)

    def _synchronize_profile(
        self,
        current_user: AuthenticatedUser,
    ) -> UserProfile:
        profile = self.db.scalar(
            select(UserProfile).where(
                UserProfile.keycloak_subject
                == current_user.subject
            )
        )
        now = datetime.now(UTC)

        if profile is None:
            profile = UserProfile(
                keycloak_subject=current_user.subject,
                username=current_user.username,
                email=current_user.email,
                display_name=(
                    current_user.display_name
                    or current_user.username
                ),
                last_seen_at=now,
            )
            self.db.add(profile)
            return profile

        if current_user.username is not None:
            profile.username = current_user.username

        if current_user.email is not None:
            profile.email = current_user.email

        profile.last_seen_at = now

        return profile

    def _commit(self, profile: UserProfile) -> None:
        self.db.commit()
        self.db.refresh(profile)

    @staticmethod
    def _response(
        profile: UserProfile,
        current_user: AuthenticatedUser,
    ) -> CurrentUserProfileResponse:
        return CurrentUserProfileResponse(
            profile_id=profile.id,
            username=profile.username,
            email=profile.email,
            email_verified=current_user.email_verified,
            display_name=profile.display_name,
            roles=sorted(current_user.effective_roles),
            last_seen_at=profile.last_seen_at,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
        )