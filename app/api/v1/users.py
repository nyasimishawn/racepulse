from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_keycloak_admin_client
from app.api.v1.user_account_errors import raise_user_account_http_error
from app.core.config import settings
from app.core.security import (
    AuthenticatedUser,
    get_active_current_user,
    require_roles,
)
from app.db.database import get_db
from app.schemas.user import (
    AccountActionResponse,
    AdminUserListResponse,
    AdminUserBulkSynchronizeResponse,
    AdminUserProfileResponse,
    AdminUserRoleUpdate,
    AdminUserStatusUpdate,
    CurrentUserProfileResponse,
    CurrentUserProfileUpdate,
)
from app.services.keycloak_admin_client import KeycloakAdminClient
from app.services.user_account_service import UserAccountService
from app.services.user_profile_service import UserProfileService


router = APIRouter(prefix="/users", tags=["Users"])


@router.get(
    "/me",
    response_model=CurrentUserProfileResponse,
    summary="Get and synchronize the authenticated user's RacePulse profile",
)
def get_my_profile(
    current_user: AuthenticatedUser = Depends(get_active_current_user),
    db: Session = Depends(get_db),
) -> CurrentUserProfileResponse:
    return UserProfileService(db).get_or_create_profile(current_user)


@router.post(
    "/me/sync",
    response_model=CurrentUserProfileResponse,
    summary="Synchronize the current Keycloak token into the RacePulse profile",
)
def synchronize_my_profile(
    current_user: AuthenticatedUser = Depends(get_active_current_user),
    db: Session = Depends(get_db),
) -> CurrentUserProfileResponse:
    return UserProfileService(db).get_or_create_profile(current_user)


@router.patch(
    "/me",
    response_model=CurrentUserProfileResponse,
    summary="Update RacePulse-owned profile fields",
)
def update_my_profile(
    payload: CurrentUserProfileUpdate,
    current_user: AuthenticatedUser = Depends(get_active_current_user),
    db: Session = Depends(get_db),
) -> CurrentUserProfileResponse:
    return UserProfileService(db).update_profile(current_user, payload)


@router.get(
    "/admin",
    response_model=AdminUserListResponse,
    summary="List RacePulse user profiles for administration",
)
def list_users_for_administration(
    query: str | None = Query(default=None, max_length=120),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    current_user: AuthenticatedUser = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> AdminUserListResponse:
    del current_user
    items, total = UserProfileService(db).list_admin_profiles(
        query=query,
        page=page,
        page_size=page_size,
    )
    return AdminUserListResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post(
    "/admin/synchronize",
    response_model=AdminUserBulkSynchronizeResponse,
    summary="Import or refresh a page of Keycloak users in PostgreSQL",
)
def synchronize_keycloak_users(
    first: int = Query(default=0, ge=0),
    max_results: int = Query(default=50, ge=1, le=100),
    search: str | None = Query(default=None, max_length=120),
    current_user: AuthenticatedUser = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
    keycloak: KeycloakAdminClient = Depends(get_keycloak_admin_client),
) -> AdminUserBulkSynchronizeResponse:
    del current_user
    try:
        items = UserAccountService(
            db,
            keycloak,
            settings,
        ).synchronize_keycloak_users(
            first=first,
            max_results=max_results,
            search=search,
        )
    except Exception as error:
        raise_user_account_http_error(error)

    return AdminUserBulkSynchronizeResponse(
        items=items,
        synchronized_count=len(items),
    )

@router.post(
    "/admin/{profile_id}/synchronize",
    response_model=AdminUserProfileResponse,
    summary="Synchronize one Keycloak user into PostgreSQL",
)
def synchronize_administrative_profile(
    profile_id: UUID,
    current_user: AuthenticatedUser = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
    keycloak: KeycloakAdminClient = Depends(get_keycloak_admin_client),
) -> AdminUserProfileResponse:
    del current_user
    try:
        return UserAccountService(db, keycloak, settings).synchronize_profile(
            profile_id
        )
    except Exception as error:
        raise_user_account_http_error(error)


@router.put(
    "/admin/{profile_id}/roles",
    response_model=AdminUserProfileResponse,
    summary="Update a user's Keycloak roles and RacePulse role snapshot",
)
def update_administrative_roles(
    profile_id: UUID,
    payload: AdminUserRoleUpdate,
    current_user: AuthenticatedUser = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
    keycloak: KeycloakAdminClient = Depends(get_keycloak_admin_client),
) -> AdminUserProfileResponse:
    del current_user
    try:
        return UserAccountService(db, keycloak, settings).update_roles(
            profile_id,
            set(payload.roles),
        )
    except Exception as error:
        raise_user_account_http_error(error)


@router.put(
    "/admin/{profile_id}/status",
    response_model=AdminUserProfileResponse,
    summary="Enable or disable a user in Keycloak and PostgreSQL",
)
def update_administrative_status(
    profile_id: UUID,
    payload: AdminUserStatusUpdate,
    current_user: AuthenticatedUser = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
    keycloak: KeycloakAdminClient = Depends(get_keycloak_admin_client),
) -> AdminUserProfileResponse:
    del current_user
    try:
        return UserAccountService(db, keycloak, settings).update_enabled(
            profile_id,
            enabled=payload.enabled,
        )
    except Exception as error:
        raise_user_account_http_error(error)


@router.post(
    "/admin/{profile_id}/verification-email",
    response_model=AccountActionResponse,
    summary="Ask Keycloak to send a verification email",
)
def send_administrative_verification_email(
    profile_id: UUID,
    current_user: AuthenticatedUser = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
    keycloak: KeycloakAdminClient = Depends(get_keycloak_admin_client),
) -> AccountActionResponse:
    del current_user
    try:
        UserAccountService(
            db,
            keycloak,
            settings,
        ).send_verification_email(profile_id)
    except Exception as error:
        raise_user_account_http_error(error)

    return AccountActionResponse(
        message="Keycloak was asked to send a verification email."
    )


@router.post(
    "/admin/{profile_id}/password-reset",
    response_model=AccountActionResponse,
    summary="Ask Keycloak to send a password-reset email",
)
def send_administrative_password_reset_email(
    profile_id: UUID,
    current_user: AuthenticatedUser = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
    keycloak: KeycloakAdminClient = Depends(get_keycloak_admin_client),
) -> AccountActionResponse:
    del current_user
    try:
        UserAccountService(
            db,
            keycloak,
            settings,
        ).send_password_reset_email(profile_id)
    except Exception as error:
        raise_user_account_http_error(error)

    return AccountActionResponse(
        message="Keycloak was asked to send a password-reset email."
    )
