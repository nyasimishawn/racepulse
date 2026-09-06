from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import AuthenticatedUser
from app.models.user_profile import UserProfile
from app.schemas.user import (
    AdminUserProfileResponse,
    CurrentUserProfileResponse,
    CurrentUserProfileUpdate,
)
from app.services.keycloak_admin_client import KeycloakUser


class UserProfileNotFoundError(LookupError):
    pass


class UserProfileService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_or_create_profile(
        self,
        current_user: AuthenticatedUser,
    ) -> CurrentUserProfileResponse:
        profile = self._profile_for_subject(current_user.subject)
        if profile is None:
            profile = UserProfile(
                keycloak_subject=current_user.subject,
                display_name=(
                    current_user.display_name or current_user.username
                ),
            )
            try:
                with self.db.begin_nested():
                    self.db.add(profile)
                    self.db.flush()
            except IntegrityError:
                profile = self._profile_for_subject(current_user.subject)
                if profile is None:
                    raise

        if not profile.is_active:
            raise HTTPException(403, "This RacePulse account is disabled.")

        self._apply_authenticated_snapshot(profile, current_user)
        self._commit(profile)
        return self._current_response(profile, current_user)

    def update_profile(
        self,
        current_user: AuthenticatedUser,
        payload: CurrentUserProfileUpdate,
    ) -> CurrentUserProfileResponse:
        profile = self._profile_for_subject(current_user.subject)
        if profile is None:
            profile = UserProfile(
                keycloak_subject=current_user.subject,
                display_name=(
                    current_user.display_name or current_user.username
                ),
            )
            self.db.add(profile)

        self._apply_authenticated_snapshot(profile, current_user)
        if "display_name" in payload.model_fields_set:
            profile.display_name = payload.display_name

        self._commit(profile)
        return self._current_response(profile, current_user)

    def synchronize_keycloak_user(
        self,
        keycloak_user: KeycloakUser,
        role_names: set[str],
        *,
        initial_display_name: str | None = None,
        group_paths: list[str] | None = None,
    ) -> AdminUserProfileResponse:
        profile = self._profile_for_subject(keycloak_user.subject)
        if profile is None:
            profile = UserProfile(
                keycloak_subject=keycloak_user.subject,
                display_name=(initial_display_name or keycloak_user.username),
            )
            self.db.add(profile)

        self._apply_keycloak_snapshot(profile, keycloak_user, role_names)
        if group_paths is not None:
            profile.keycloak_groups = sorted(set(group_paths))
        self._commit(profile)
        return self._admin_response(profile)

    def get_admin_profile(self, profile_id: UUID) -> AdminUserProfileResponse:
        return self._admin_response(self._required_profile(profile_id))

    def profile_for_id(self, profile_id: UUID) -> UserProfile:
        return self._required_profile(profile_id)

    def list_admin_profiles(
        self,
        *,
        query: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[AdminUserProfileResponse], int]:
        statement = select(UserProfile)
        count_statement = select(func.count()).select_from(UserProfile)

        normalized_query = (query or "").strip()
        if normalized_query:
            pattern = f"%{normalized_query}%"
            filters = or_(
                UserProfile.username.ilike(pattern),
                UserProfile.email.ilike(pattern),
                UserProfile.display_name.ilike(pattern),
            )
            statement = statement.where(filters)
            count_statement = count_statement.where(filters)

        total = int(self.db.scalar(count_statement) or 0)
        profiles = self.db.scalars(
            statement.order_by(UserProfile.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return [self._admin_response(profile) for profile in profiles], total

    def _profile_for_subject(self, subject: str) -> UserProfile | None:
        return self.db.scalar(
            select(UserProfile).where(UserProfile.keycloak_subject == subject)
        )

    def _required_profile(self, profile_id: UUID) -> UserProfile:
        profile = self.db.get(UserProfile, profile_id)
        if profile is None:
            raise UserProfileNotFoundError("The RacePulse user was not found.")
        return profile

    @staticmethod
    def _apply_authenticated_snapshot(
        profile: UserProfile,
        current_user: AuthenticatedUser,
    ) -> None:
        now = datetime.now(UTC)
        # Some access-token configurations omit profile or email claims. Do
        # not erase a richer snapshot previously obtained from Keycloak.
        if current_user.username is not None:
            profile.username = current_user.username
        if current_user.email is not None:
            profile.email = current_user.email
            profile.email_verified = current_user.email_verified
        profile.keycloak_roles = sorted(current_user.realm_roles)
        profile.keycloak_client_roles = sorted(current_user.client_roles)
        if current_user.groups is not None:
            profile.keycloak_groups = sorted(current_user.groups)
        if current_user.given_name is not None:
            profile.given_name = current_user.given_name
        if current_user.family_name is not None:
            profile.family_name = current_user.family_name
        profile.identity_synced_at = now
        profile.last_seen_at = now

    @staticmethod
    def _apply_keycloak_snapshot(
        profile: UserProfile,
        keycloak_user: KeycloakUser,
        role_names: set[str],
    ) -> None:
        profile.username = keycloak_user.username
        profile.email = keycloak_user.email
        profile.email_verified = keycloak_user.email_verified
        profile.is_active = keycloak_user.enabled
        profile.given_name = keycloak_user.given_name
        profile.family_name = keycloak_user.family_name
        profile.keycloak_roles = sorted(
            {role.strip().casefold() for role in role_names if role.strip()}
        )
        profile.identity_synced_at = datetime.now(UTC)

    def _commit(self, profile: UserProfile) -> None:
        self.db.commit()
        self.db.refresh(profile)

    @staticmethod
    def _current_response(
        profile: UserProfile,
        current_user: AuthenticatedUser,
    ) -> CurrentUserProfileResponse:
        return CurrentUserProfileResponse(
            profile_id=profile.id,
            username=profile.username,
            email=profile.email,
            email_verified=profile.email_verified,
            display_name=profile.display_name,
            roles=sorted(current_user.effective_roles),
            groups=sorted(profile.keycloak_groups),
            given_name=profile.given_name,
            family_name=profile.family_name,
            is_active=profile.is_active,
            last_seen_at=profile.last_seen_at,
            identity_synced_at=profile.identity_synced_at,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
        )

    @staticmethod
    def _admin_response(profile: UserProfile) -> AdminUserProfileResponse:
        return AdminUserProfileResponse(
            profile_id=profile.id,
            keycloak_subject=profile.keycloak_subject,
            username=profile.username,
            email=profile.email,
            email_verified=profile.email_verified,
            display_name=profile.display_name,
            keycloak_roles=sorted(profile.keycloak_roles),
            keycloak_client_roles=sorted(profile.keycloak_client_roles),
            keycloak_groups=sorted(profile.keycloak_groups),
            given_name=profile.given_name,
            family_name=profile.family_name,
            is_active=profile.is_active,
            last_seen_at=profile.last_seen_at,
            identity_synced_at=profile.identity_synced_at,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
        )
