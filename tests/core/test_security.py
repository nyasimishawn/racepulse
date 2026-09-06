import pytest
from fastapi import HTTPException, status

from app.core.config import settings
from app.core.security import (
    authenticated_user_from_claims,
    keycloak_oauth2_scheme,
    require_roles,
)


def test_keycloak_oauth_scheme_exposes_authorization_code_flow() -> None:
    authorization_code = keycloak_oauth2_scheme.model.flows.authorizationCode

    assert authorization_code is not None
    assert authorization_code.authorizationUrl == (
        settings.keycloak_authorization_url
    )
    assert authorization_code.tokenUrl == settings.keycloak_token_url
    assert authorization_code.scopes["openid"] == "OpenID Connect identity"


def test_role_hierarchy_is_owned_by_keycloak() -> None:
    user = authenticated_user_from_claims(
        {
            "sub": "keycloak-user-id",
            "typ": "Bearer",
            "preferred_username": "shawn",
            "realm_access": {"roles": ["admin"]},
        }
    )

    assert user.realm_roles == frozenset({"admin"})
    assert user.effective_roles == frozenset({"admin"})
    assert user.has_role("editor") is False
    assert user.has_role("fan") is False


def test_keycloak_composites_client_roles_and_groups(monkeypatch) -> None:
    monkeypatch.setattr(settings, "keycloak_audience", "racepulse")
    user = authenticated_user_from_claims({
        "sub": "keycloak-user-id", "typ": "Bearer",
        "realm_access": {"roles": ["admin", "editor", "fan"]},
        "resource_access": {
            "racepulse": {"roles": ["analyst"]},
            "unrelated-client": {"roles": ["superuser"]},
        },
        "groups": ["/Fans", "/Staff/Editors", "/Fans"],
    })
    assert user.effective_roles == frozenset({"admin", "editor", "fan", "analyst"})
    assert user.groups == frozenset({"/Fans", "/Staff/Editors"})
    assert not user.has_role("superuser")


def test_fan_cannot_access_editor_only_dependency() -> None:
    user = authenticated_user_from_claims(
        {
            "sub": "keycloak-user-id",
            "typ": "Bearer",
            "realm_access": {"roles": ["fan"]},
        }
    )
    dependency = require_roles("editor")

    with pytest.raises(HTTPException) as raised:
        dependency(user)

    assert raised.value.status_code == status.HTTP_403_FORBIDDEN
