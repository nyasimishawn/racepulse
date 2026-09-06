"""Public OIDC configuration and the post-Keycloak sign-in handshake."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import AuthenticatedUser, get_active_current_user
from app.db.database import get_db
from app.schemas.user import CurrentUserProfileResponse
from app.services.user_profile_service import UserProfileService


router = APIRouter(prefix="/auth", tags=["Authentication"])


class LoginConfiguration(BaseModel):
    issuer: str
    discovery_url: str
    client_id: str
    scopes: list[str]
    response_type: str = "code"
    code_challenge_method: str = "S256"


@router.get("/config", response_model=LoginConfiguration)
def get_login_configuration() -> LoginConfiguration:
    if (
        not settings.keycloak_configured
        or not settings.keycloak_public_client_id
    ):
        raise HTTPException(503, "Keycloak sign-in is not configured.")
    issuer = settings.keycloak_issuer_url.rstrip("/")
    return LoginConfiguration(
        issuer=issuer,
        discovery_url=f"{issuer}/.well-known/openid-configuration",
        client_id=settings.keycloak_public_client_id,
        scopes=["openid", "profile", "email"],
    )


@router.post(
    "/session",
    response_model=CurrentUserProfileResponse,
    summary="Complete Keycloak sign-in and synchronize the local profile",
)
def synchronize_login(
    current_user: AuthenticatedUser = Depends(get_active_current_user),
    db: Session = Depends(get_db),
) -> CurrentUserProfileResponse:
    return UserProfileService(db).get_or_create_profile(current_user)
