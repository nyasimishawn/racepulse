from collections.abc import Generator

from fastapi import HTTPException, status

from app.core.config import settings
from app.services.keycloak_admin_client import (
    KeycloakAdminClient,
    KeycloakAdminConfigurationError,
)


def get_keycloak_admin_client() -> Generator[KeycloakAdminClient, None, None]:
    try:
        client = KeycloakAdminClient(settings)
    except KeycloakAdminConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RacePulse account administration is not configured.",
        ) from error

    try:
        yield client
    finally:
        client.close()


def get_registration_keycloak_admin_client() -> Generator[
    KeycloakAdminClient,
    None,
    None,
]:
    if not settings.keycloak_public_registration_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Public RacePulse account registration is disabled.",
        )

    yield from get_keycloak_admin_client()
