from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.schemas.user import (
    AccountRegistrationRequest,
    AdminUserProfileResponse,
)
from app.services.keycloak_admin_client import (
    KeycloakAdminClient,
    KeycloakAdminError,
)
from app.services.user_profile_service import UserProfileService


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    profile: AdminUserProfileResponse
    verification_email_sent: bool


class UserAccountService:
    def __init__(
        self,
        db: Session,
        keycloak: KeycloakAdminClient,
        settings: Settings,
    ) -> None:
        self.db = db
        self.keycloak = keycloak
        self.settings = settings
        self.profiles = UserProfileService(db)

    def register(
        self,
        payload: AccountRegistrationRequest,
    ) -> RegistrationResult:
        keycloak_user = None

        try:
            keycloak_user = self.keycloak.create_user(
                username=payload.username,
                email=str(payload.email),
            )
            self.keycloak.set_password(
                keycloak_user.subject,
                payload.password.get_secret_value(),
            )
            roles = self.keycloak.add_realm_roles(
                keycloak_user.subject,
                {self.settings.keycloak_default_role},
            )
            profile = self.profiles.synchronize_keycloak_user(
                keycloak_user,
                roles,
                initial_display_name=payload.display_name,
                group_paths=self.keycloak.get_user_group_paths(
                    keycloak_user.subject
                ),
            )
        except Exception:
            self.db.rollback()
            if keycloak_user is not None:
                self._delete_keycloak_user_after_failed_registration(
                    keycloak_user.subject
                )
            raise

        verification_email_sent = False
        if self.settings.keycloak_send_verification_email:
            try:
                self.keycloak.send_verification_email(keycloak_user.subject)
                verification_email_sent = True
            except KeycloakAdminError:
                # Registration succeeded. Do not delete an account merely
                # because Keycloak SMTP is not configured yet.
                logger.warning(
                    "RacePulse account was created but Keycloak could not "
                    "send a verification email."
                )

        return RegistrationResult(
            profile=profile,
            verification_email_sent=verification_email_sent,
        )

    def synchronize_profile(
        self, profile_id: UUID
    ) -> AdminUserProfileResponse:
        profile = self.profiles.profile_for_id(profile_id)
        keycloak_user = self.keycloak.get_user(profile.keycloak_subject)
        roles = self.keycloak.get_realm_role_names(keycloak_user.subject)
        return self.profiles.synchronize_keycloak_user(
            keycloak_user,
            roles,
            group_paths=self.keycloak.get_user_group_paths(
                keycloak_user.subject
            ),
        )

    def synchronize_keycloak_users(
        self,
        *,
        first: int,
        max_results: int,
        search: str | None,
    ) -> list[AdminUserProfileResponse]:
        synchronized: list[AdminUserProfileResponse] = []
        keycloak_users = self.keycloak.list_users(
            first=first,
            max_results=max_results,
            search=search,
        )

        for keycloak_user in keycloak_users:
            roles = self.keycloak.get_realm_role_names(keycloak_user.subject)
            synchronized.append(
                self.profiles.synchronize_keycloak_user(
                    keycloak_user,
                    roles,
                    group_paths=self.keycloak.get_user_group_paths(
                        keycloak_user.subject
                    ),
                )
            )

        return synchronized

    def update_roles(
        self,
        profile_id: UUID,
        desired_roles: set[str],
    ) -> AdminUserProfileResponse:
        profile = self.profiles.profile_for_id(profile_id)
        roles = self.keycloak.replace_managed_realm_roles(
            profile.keycloak_subject,
            desired_roles=desired_roles,
            managed_roles=self.settings.keycloak_assignable_role_names,
        )
        keycloak_user = self.keycloak.get_user(profile.keycloak_subject)
        return self.profiles.synchronize_keycloak_user(
            keycloak_user,
            roles,
            group_paths=self.keycloak.get_user_group_paths(
                keycloak_user.subject
            ),
        )

    def update_enabled(
        self,
        profile_id: UUID,
        *,
        enabled: bool,
    ) -> AdminUserProfileResponse:
        profile = self.profiles.profile_for_id(profile_id)
        keycloak_user = self.keycloak.set_user_enabled(
            profile.keycloak_subject,
            enabled=enabled,
        )
        if not enabled:
            # Keycloak has already confirmed the disable. Persist the local
            # kill switch before any further remote call can fail, so an
            # already-issued token cannot remain usable in RacePulse.
            profile.is_active = False
            profile.identity_synced_at = datetime.now(UTC)
            self.db.commit()
            self.db.refresh(profile)
            return self.profiles.get_admin_profile(profile.id)

        roles = self.keycloak.get_realm_role_names(keycloak_user.subject)
        return self.profiles.synchronize_keycloak_user(
            keycloak_user,
            roles,
            group_paths=self.keycloak.get_user_group_paths(
                keycloak_user.subject
            ),
        )

    def send_verification_email(self, profile_id: UUID) -> None:
        profile = self.profiles.profile_for_id(profile_id)
        self.keycloak.send_verification_email(profile.keycloak_subject)

    def send_password_reset_email(self, profile_id: UUID) -> None:
        profile = self.profiles.profile_for_id(profile_id)
        self.keycloak.send_password_reset_email(profile.keycloak_subject)

    def _delete_keycloak_user_after_failed_registration(
        self,
        subject: str,
    ) -> None:
        try:
            self.keycloak.delete_user(subject)
        except KeycloakAdminError:
            logger.warning(
                "Could not roll back a Keycloak user after RacePulse "
                "registration failed."
            )
