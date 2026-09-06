from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.user_profile import UserProfile
from app.schemas.user import AccountRegistrationRequest
from app.services.keycloak_admin_client import (
    KeycloakAdminConflictError,
    KeycloakAdminRequestError,
    KeycloakUser,
)
from app.services.user_account_service import UserAccountService


class FakeKeycloakAdminClient:
    def __init__(self) -> None:
        self.users: dict[str, KeycloakUser] = {}
        self.roles: dict[str, set[str]] = {}
        self.passwords: dict[str, str] = {}
        self.deleted_subjects: list[str] = []
        self.verification_subjects: list[str] = []
        self.fail_on_role_assignment = False
        self.fail_on_role_lookup = False

    def create_user(self, *, username: str, email: str) -> KeycloakUser:
        if any(
            user.username == username or user.email == email
            for user in self.users.values()
        ):
            raise KeycloakAdminConflictError("Duplicate Keycloak identity.")

        subject = f"user-{len(self.users) + 1}"
        user = KeycloakUser(
            subject=subject,
            username=username,
            email=email,
            email_verified=False,
            enabled=True,
        )
        self.users[subject] = user
        self.roles[subject] = set()
        return user

    def delete_user(self, subject: str) -> None:
        self.deleted_subjects.append(subject)
        self.users.pop(subject, None)
        self.roles.pop(subject, None)
        self.passwords.pop(subject, None)

    def set_password(self, subject: str, password: str) -> None:
        self.passwords[subject] = password

    def add_realm_roles(
        self,
        subject: str,
        role_names: set[str],
    ) -> set[str]:
        if self.fail_on_role_assignment:
            raise KeycloakAdminRequestError("Role assignment failed.")

        self.roles[subject].update(role_names)
        return set(self.roles[subject])

    def get_user(self, subject: str) -> KeycloakUser:
        return self.users[subject]

    def get_realm_role_names(self, subject: str) -> set[str]:
        if self.fail_on_role_lookup:
            raise KeycloakAdminRequestError("Role lookup failed.")
        return set(self.roles[subject])

    def get_user_group_paths(self, subject: str) -> list[str]:
        return ["/Fans"]

    def set_user_enabled(
        self,
        subject: str,
        *,
        enabled: bool,
    ) -> KeycloakUser:
        current = self.users[subject]
        updated = KeycloakUser(
            subject=current.subject,
            username=current.username,
            email=current.email,
            email_verified=current.email_verified,
            enabled=enabled,
        )
        self.users[subject] = updated
        return updated

    def list_users(
        self,
        *,
        first: int,
        max_results: int,
        search: str | None = None,
    ) -> list[KeycloakUser]:
        users = list(self.users.values())
        if search is not None:
            normalized_search = search.casefold()
            users = [
                user
                for user in users
                if normalized_search
                in " ".join(
                    value
                    for value in (user.username, user.email)
                    if value is not None
                ).casefold()
            ]
        return users[first : first + max_results]

    def send_verification_email(self, subject: str) -> None:
        self.verification_subjects.append(subject)


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "keycloak_enabled": True,
        "keycloak_issuer_url": "http://localhost:1738/realms/RacePulse",
        "keycloak_audience": "RacePulse",
        "keycloak_default_role": "fan",
        "keycloak_assignable_roles": "fan,editor,admin",
    }
    values.update(overrides)
    return Settings(**values)


def make_registration_request() -> AccountRegistrationRequest:
    return AccountRegistrationRequest(
        username="shawn",
        email="shawn@example.com",
        display_name="Shawn Matunda",
        password="CorrectHorseBatteryStaple",
    )


def test_registration_creates_keycloak_identity_and_profile(
    db_session: Session,
) -> None:
    keycloak = FakeKeycloakAdminClient()
    service = UserAccountService(
        db_session,
        keycloak,  # type: ignore[arg-type]
        make_settings(keycloak_send_verification_email=True),
    )

    result = service.register(make_registration_request())

    profile = db_session.get(UserProfile, result.profile.profile_id)

    assert profile is not None
    assert profile.keycloak_subject == "user-1"
    assert profile.username == "shawn"
    assert profile.email == "shawn@example.com"
    assert profile.display_name == "Shawn Matunda"
    assert profile.keycloak_roles == ["fan"]
    assert profile.keycloak_groups == ["/Fans"]
    assert keycloak.passwords == {
        "user-1": "CorrectHorseBatteryStaple",
    }
    assert keycloak.verification_subjects == ["user-1"]
    assert result.verification_email_sent is True


def test_registration_removes_keycloak_user_when_profile_sync_fails(
    db_session: Session,
) -> None:
    keycloak = FakeKeycloakAdminClient()
    keycloak.fail_on_role_assignment = True
    service = UserAccountService(
        db_session,
        keycloak,  # type: ignore[arg-type]
        make_settings(),
    )

    with pytest.raises(KeycloakAdminRequestError):
        service.register(make_registration_request())

    assert db_session.scalars(select(UserProfile)).all() == []
    assert keycloak.deleted_subjects == ["user-1"]
    assert keycloak.users == {}


def test_administrator_can_bulk_synchronize_existing_keycloak_users(
    db_session: Session,
) -> None:
    keycloak = FakeKeycloakAdminClient()
    first = keycloak.create_user(
        username="shawn",
        email="shawn@example.com",
    )
    second = keycloak.create_user(
        username="maria",
        email="maria@example.com",
    )
    keycloak.roles[first.subject].update({"fan"})
    keycloak.roles[second.subject].update({"fan", "editor"})

    service = UserAccountService(
        db_session,
        keycloak,  # type: ignore[arg-type]
        make_settings(),
    )

    synchronized = service.synchronize_keycloak_users(
        first=0,
        max_results=50,
        search=None,
    )

    assert [profile.username for profile in synchronized] == [
        "shawn",
        "maria",
    ]
    assert synchronized[1].keycloak_roles == ["editor", "fan"]
    assert len(db_session.scalars(select(UserProfile)).all()) == 2


def test_disable_fails_closed_before_follow_up_keycloak_reads(
    db_session: Session,
) -> None:
    keycloak = FakeKeycloakAdminClient()
    user = keycloak.create_user(
        username="shawn",
        email="shawn@example.com",
    )
    profile = UserProfile(
        keycloak_subject=user.subject,
        username=user.username,
        email=user.email,
        is_active=True,
        keycloak_roles=["fan"],
    )
    db_session.add(profile)
    db_session.commit()

    keycloak.fail_on_role_lookup = True
    service = UserAccountService(
        db_session,
        keycloak,  # type: ignore[arg-type]
        make_settings(),
    )

    response = service.update_enabled(profile.id, enabled=False)

    db_session.refresh(profile)
    assert response.is_active is False
    assert profile.is_active is False
    assert keycloak.users[user.subject].enabled is False

