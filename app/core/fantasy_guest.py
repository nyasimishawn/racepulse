"""Temporary development-only Fantasy guest identities."""

from hashlib import sha256
import secrets

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    AuthenticatedUser,
    authenticate_access_token,
    ensure_active_profile,
    keycloak_oauth2_scheme,
)
from app.db.database import get_db
from app.models.user_profile import UserProfile
from app.services.user_profile_service import UserProfileService


GUEST_KEY_HEADER = "X-Fantasy-Guest-Key"


def _guest_subject(key: str) -> str:
    return f"fantasy-guest:{sha256(key.encode('ascii')).hexdigest()}"


def create_guest(db: Session) -> str:
    if not settings.fantasy_guest_access_enabled:
        raise HTTPException(404, "Fantasy guest mode is unavailable.")
    key = secrets.token_urlsafe(32)
    db.add(
        UserProfile(keycloak_subject=_guest_subject(key), display_name="Guest")
    )
    db.commit()
    return key


def get_fantasy_fan(
    access_token: str | None = Depends(keycloak_oauth2_scheme),
    guest_key: str | None = Header(default=None, alias=GUEST_KEY_HEADER),
    db: Session = Depends(get_db),
) -> AuthenticatedUser:
    if access_token:
        user = authenticate_access_token(access_token)
        ensure_active_profile(user, db)
        if not user.has_role("fan"):
            raise HTTPException(
                403, "You do not have permission to use this resource."
            )
        UserProfileService(db).get_or_create_profile(user)
        return user
    if not settings.fantasy_guest_access_enabled or guest_key is None:
        raise HTTPException(
            401, "A fan token or Fantasy guest key is required."
        )
    try:
        encoded = guest_key.encode("ascii")
    except UnicodeEncodeError as error:
        raise HTTPException(401, "Invalid Fantasy guest key.") from error
    if len(encoded) != 43 or not all(
        char
        in b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        for char in encoded
    ):
        raise HTTPException(401, "Invalid Fantasy guest key.")
    profile = db.scalar(
        select(UserProfile).where(
            UserProfile.keycloak_subject == _guest_subject(guest_key)
        )
    )
    if profile is None:
        raise HTTPException(401, "Fantasy guest key was not found.")
    if not profile.is_active:
        raise HTTPException(403, "This RacePulse account is disabled.")
    return AuthenticatedUser(
        subject=profile.keycloak_subject,
        username=None,
        email=None,
        email_verified=False,
        display_name=profile.display_name or "Guest",
        realm_roles=frozenset({"fan"}),
    )
