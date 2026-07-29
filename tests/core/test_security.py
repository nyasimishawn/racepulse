import pytest
from fastapi import HTTPException, status

from app.core.security import (
    authenticated_user_from_claims,
    require_roles,
)


def test_admin_receives_the_effective_role_hierarchy() -> None:
    user = authenticated_user_from_claims(
        {
            "sub": "keycloak-user-id",
            "typ": "Bearer",
            "preferred_username": "shawn",
            "realm_access": {"roles": ["admin"]},
        }
    )

    assert user.realm_roles == frozenset({"admin"})
    assert user.effective_roles == frozenset(
        {"admin", "editor", "fan"}
    )
    assert user.has_role("editor") is True
    assert user.has_role("fan") is True


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