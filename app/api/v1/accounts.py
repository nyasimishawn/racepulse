from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_registration_keycloak_admin_client
from app.api.v1.user_account_errors import raise_user_account_http_error
from app.core.config import settings
from app.core.rate_limit import registration_rate_limit
from app.db.database import get_db
from app.schemas.user import (
    AccountRegistrationResponse,
    AccountRegistrationRequest,
    CurrentUserProfileResponse,
)
from app.services.keycloak_admin_client import KeycloakAdminClient
from app.services.user_account_service import UserAccountService


router = APIRouter(prefix="/accounts", tags=["Accounts"])


@router.post(
    "/register",
    response_model=AccountRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a RacePulse account in Keycloak and PostgreSQL",
    deprecated=True,
)
def register_account(
    payload: AccountRegistrationRequest,
    _: None = Depends(registration_rate_limit),
    db: Session = Depends(get_db),
    keycloak: KeycloakAdminClient = Depends(
        get_registration_keycloak_admin_client
    ),
) -> AccountRegistrationResponse:

    try:
        result = UserAccountService(db, keycloak, settings).register(payload)
    except Exception as error:
        raise_user_account_http_error(error)

    profile = result.profile
    return AccountRegistrationResponse(
        profile=CurrentUserProfileResponse(
            profile_id=profile.profile_id,
            username=profile.username,
            email=profile.email,
            email_verified=profile.email_verified,
            display_name=profile.display_name,
            roles=profile.keycloak_roles,
            groups=profile.keycloak_groups,
            given_name=profile.given_name,
            family_name=profile.family_name,
            is_active=profile.is_active,
            last_seen_at=profile.last_seen_at,
            identity_synced_at=profile.identity_synced_at,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
        ),
        verification_email_sent=result.verification_email_sent,
    )
