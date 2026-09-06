from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
import logging
from typing import Any

import jwt
from app.core.config import settings
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2AuthorizationCodeBearer
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError, PyJWKClientError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.user_profile import UserProfile


logger = logging.getLogger(__name__)


keycloak_oauth2_scheme = OAuth2AuthorizationCodeBearer(
    authorizationUrl=settings.keycloak_authorization_url,
    tokenUrl=settings.keycloak_token_url,
    scopes={
        "openid": "OpenID Connect identity",
        "profile": "Basic profile",
        "email": "Email address",
    },
    scheme_name="KeycloakOAuth2",
    auto_error=False,
)


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    subject: str
    username: str | None
    email: str | None
    email_verified: bool
    display_name: str | None
    realm_roles: frozenset[str]
    client_roles: frozenset[str] = frozenset()
    # None means the mapper omitted the claim; an empty set clears membership.
    groups: frozenset[str] | None = None
    given_name: str | None = None
    family_name: str | None = None

    @property
    def effective_roles(self) -> frozenset[str]:
        # Composite roles and group role mappings are expanded by Keycloak.
        return self.realm_roles | self.client_roles

    def has_role(self, role: str) -> bool:
        normalized_role = _normalize_role(role)

        return (
            normalized_role is not None
            and normalized_role in self.effective_roles
        )


@lru_cache
def _get_jwk_client(jwks_url: str) -> PyJWKClient:
    return PyJWKClient(jwks_url)


def get_current_user(
    access_token: str | None = Depends(keycloak_oauth2_scheme),
) -> AuthenticatedUser:
    if access_token is None:
        logger.warning(
            "Rejected RacePulse API request: no Bearer access token was supplied."
        )
        raise _unauthorized()

    return authenticate_access_token(access_token)


def authenticate_access_token(access_token: str) -> AuthenticatedUser:
    """Validate a raw Bearer token for HTTP or WebSocket entry points."""
    if not access_token:
        raise _unauthorized()

    if not settings.keycloak_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Keycloak authentication is not configured.",
        )

    try:
        signing_key = _get_jwk_client(
            settings.keycloak_jwks_url
        ).get_signing_key_from_jwt(access_token)

        claims = jwt.decode(
            access_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.keycloak_audience,
            issuer=settings.keycloak_issuer_url.rstrip("/"),
            leeway=settings.keycloak_clock_skew_seconds,
            options={
                "require": [
                    "aud",
                    "exp",
                    "iat",
                    "iss",
                    "sub",
                    "typ",
                ],
            },
        )

        return authenticated_user_from_claims(claims)

    except PyJWKClientError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication signing keys are unavailable.",
        ) from error

    except (InvalidTokenError, ValueError) as error:
        # Never include a JWT or its decoded content in application logs.
        logger.warning(
            "Rejected Keycloak access token (%s).",
            type(error).__name__,
        )

        raise _unauthorized() from error


def authenticated_user_from_claims(
    claims: Mapping[str, Any],
) -> AuthenticatedUser:
    subject = _optional_text(claims, "sub")

    if subject is None:
        raise ValueError("Access token has no subject claim.")

    if _optional_text(claims, "typ") != "Bearer":
        raise ValueError("Token is not an access token.")

    username = _optional_text(claims, "preferred_username")
    display_name = _optional_text(claims, "name") or username

    return AuthenticatedUser(
        subject=subject,
        username=username,
        email=_optional_text(claims, "email"),
        email_verified=claims.get("email_verified") is True,
        display_name=display_name,
        realm_roles=_realm_roles(claims),
        client_roles=_client_roles(claims),
        groups=_groups(claims),
        given_name=_optional_text(claims, "given_name"),
        family_name=_optional_text(claims, "family_name"),
    )


def get_active_current_user(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AuthenticatedUser:
    ensure_active_profile(current_user, db)
    # Import locally to keep identity parsing independent of domain services.
    from app.services.user_profile_service import UserProfileService

    UserProfileService(db).get_or_create_profile(current_user)
    return current_user


def ensure_active_profile(
    current_user: AuthenticatedUser,
    db: Session,
) -> AuthenticatedUser:
    """Reject a disabled RacePulse profile without replacing Keycloak.

    Keycloak remains the authority for credentials and realm roles. The local
    profile is only a RacePulse access kill-switch and domain snapshot. A
    missing profile is allowed so a valid legacy Keycloak user can bootstrap
    through ``/users/me``; an existing disabled profile is always rejected.
    """
    profile = db.scalar(
        select(UserProfile).where(
            UserProfile.keycloak_subject == current_user.subject
        )
    )

    if profile is not None and not profile.is_active:
        logger.info("Rejected disabled RacePulse profile.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This RacePulse account is disabled.",
        )

    return current_user


def require_roles(
    *required_roles: str,
) -> Callable[..., AuthenticatedUser]:
    allowed_roles = frozenset(
        role
        for value in required_roles
        if (role := _normalize_role(value)) is not None
    )

    if not allowed_roles:
        raise ValueError("At least one role is required.")

    def dependency(
        current_user: AuthenticatedUser = Depends(get_active_current_user),
    ) -> AuthenticatedUser:
        if not current_user.effective_roles.intersection(allowed_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to use this resource.",
            )

        return current_user

    return dependency


def _realm_roles(
    claims: Mapping[str, Any],
) -> frozenset[str]:
    realm_access = claims.get("realm_access")

    if not isinstance(realm_access, Mapping):
        return frozenset()

    role_values = realm_access.get("roles")

    if not isinstance(role_values, list):
        return frozenset()

    roles: list[str] = []

    for value in role_values:
        role = _normalize_role(value)

        if role is not None:
            roles.append(role)

    return frozenset(roles)


def _client_roles(claims: Mapping[str, Any]) -> frozenset[str]:
    resources = claims.get("resource_access")
    if not isinstance(resources, Mapping):
        return frozenset()
    # Roles for other audiences must never grant access to RacePulse.
    resource = resources.get(settings.keycloak_audience)
    if not isinstance(resource, Mapping):
        return frozenset()
    return _realm_roles({"realm_access": resource})


def _groups(claims: Mapping[str, Any]) -> frozenset[str] | None:
    values = claims.get(settings.keycloak_groups_claim)
    if values is None:
        return None
    if not isinstance(values, list) or any(
        not isinstance(value, str) for value in values
    ):
        raise ValueError("Access token has an invalid groups claim.")
    return frozenset(value.strip() for value in values if value.strip())


def _optional_text(
    claims: Mapping[str, Any],
    name: str,
) -> str | None:
    value = claims.get(name)

    if not isinstance(value, str):
        return None

    value = value.strip()

    return value or None


def _normalize_role(value: object) -> str | None:
    if not isinstance(value, str):
        return None

    role = value.strip().casefold()

    return role or None


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="A valid access token is required.",
        headers={"WWW-Authenticate": "Bearer"},
    )
