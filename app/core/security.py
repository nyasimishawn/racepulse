from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError, PyJWKClientError

from app.core.config import settings


bearer_scheme = HTTPBearer(auto_error=False)

ROLE_IMPLICATIONS: dict[str, frozenset[str]] = {
    "admin": frozenset({"admin", "editor", "fan"}),
    "editor": frozenset({"editor", "fan"}),
    "fan": frozenset({"fan"}),
}


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    subject: str
    username: str | None
    email: str | None
    email_verified: bool
    display_name: str | None
    realm_roles: frozenset[str]

    @property
    def effective_roles(self) -> frozenset[str]:
        effective = set(self.realm_roles)

        for role in self.realm_roles:
            effective.update(ROLE_IMPLICATIONS.get(role, ()))

        return frozenset(effective)

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
    credentials: HTTPAuthorizationCredentials | None = Depends(
        bearer_scheme
    ),
) -> AuthenticatedUser:
    if (
        credentials is None
        or credentials.scheme.casefold() != "bearer"
    ):
        raise _unauthorized()

    if not settings.keycloak_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Keycloak authentication is not configured.",
        )

    try:
        signing_key = _get_jwk_client(
            settings.keycloak_jwks_url
        ).get_signing_key_from_jwt(credentials.credentials)

        claims = jwt.decode(
            credentials.credentials,
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
    )


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
        current_user: AuthenticatedUser = Depends(get_current_user),
    ) -> AuthenticatedUser:
        if not current_user.effective_roles.intersection(
            allowed_roles
        ):
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