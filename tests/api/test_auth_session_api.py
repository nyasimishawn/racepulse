from dataclasses import replace

from sqlalchemy import select

from app.core.config import settings
from app.core.security import AuthenticatedUser, get_current_user
from app.main import app
from app.models.user_profile import UserProfile


def identity(**changes):
    user = AuthenticatedUser(
        subject="keycloak-login-subject",
        username="fan",
        email="fan@example.com",
        email_verified=True,
        display_name="Race Fan",
        realm_roles=frozenset({"fan"}),
        client_roles=frozenset({"analyst"}),
        groups=frozenset({"/Fans", "/Monza"}),
        given_name="Race",
        family_name="Fan",
    )
    return replace(user, **changes)


def test_login_sync_creates_one_profile_and_refreshes_keycloak_fields(
    api_client, db_session
):
    app.dependency_overrides[get_current_user] = lambda: identity()
    first = api_client.post("/api/v1/auth/session")
    assert first.status_code == 200
    assert first.json()["groups"] == ["/Fans", "/Monza"]
    assert first.json()["roles"] == ["analyst", "fan"]
    assert first.json()["given_name"] == "Race"

    app.dependency_overrides[get_current_user] = lambda: identity(
        username="new-name",
        groups=frozenset(),
        realm_roles=frozenset({"editor", "fan"}),
        client_roles=frozenset(),
    )
    second = api_client.post("/api/v1/auth/session")
    assert second.status_code == 200
    assert second.json()["profile_id"] == first.json()["profile_id"]
    profiles = db_session.scalars(select(UserProfile)).all()
    assert len(profiles) == 1
    assert profiles[0].username == "new-name"
    assert profiles[0].keycloak_roles == ["editor", "fan"]
    assert profiles[0].keycloak_client_roles == []
    assert profiles[0].keycloak_groups == []


def test_missing_group_mapper_preserves_known_membership(
    api_client, db_session
):
    app.dependency_overrides[get_current_user] = lambda: identity()
    api_client.post("/api/v1/auth/session")
    app.dependency_overrides[get_current_user] = lambda: identity(groups=None)
    response = api_client.post("/api/v1/auth/session")
    assert response.json()["groups"] == ["/Fans", "/Monza"]


def test_login_rejects_missing_auth_and_disabled_profiles(
    api_client, db_session
):
    assert api_client.post("/api/v1/auth/session").status_code == 401
    db_session.add(
        UserProfile(keycloak_subject="keycloak-login-subject", is_active=False)
    )
    db_session.commit()
    app.dependency_overrides[get_current_user] = lambda: identity()
    assert api_client.post("/api/v1/auth/session").status_code == 403


def test_login_configuration_contains_only_public_pkce_settings(
    api_client, monkeypatch
):
    monkeypatch.setattr(settings, "keycloak_enabled", True)
    monkeypatch.setattr(
        settings,
        "keycloak_issuer_url",
        "https://identity.example/realms/racepulse",
    )
    monkeypatch.setattr(settings, "keycloak_audience", "racepulse")
    monkeypatch.setattr(settings, "keycloak_public_client_id", "racepulse-app")
    monkeypatch.setattr(
        settings, "keycloak_admin_client_secret", "not-for-clients"
    )
    response = api_client.get("/api/v1/auth/config")
    assert response.status_code == 200
    assert response.json()["client_id"] == "racepulse-app"
    assert response.json()["code_challenge_method"] == "S256"
    assert "not-for-clients" not in response.text
