from typing import NoReturn

from fastapi import HTTPException, status

from app.services.keycloak_admin_client import (
    KeycloakAdminConfigurationError,
    KeycloakAdminConflictError,
    KeycloakAdminError,
    KeycloakAdminNotFoundError,
    KeycloakAdminPermissionError,
    KeycloakAdminRequestError,
    KeycloakAdminUnavailableError,
)
from app.services.user_profile_service import UserProfileNotFoundError


def raise_user_account_http_error(error: Exception) -> NoReturn:
    if isinstance(error, UserProfileNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    if isinstance(error, KeycloakAdminConflictError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    if isinstance(error, KeycloakAdminNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    if isinstance(error, KeycloakAdminRequestError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    if isinstance(
        error,
        (
            KeycloakAdminConfigurationError,
            KeycloakAdminPermissionError,
            KeycloakAdminUnavailableError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    if isinstance(error, KeycloakAdminError):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Keycloak could not complete the account operation.",
        ) from error

    raise error
