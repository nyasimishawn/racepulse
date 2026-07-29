import pytest
from fastapi.testclient import TestClient

from app.core.security import AuthenticatedUser, get_current_user
from app.main import app


@pytest.fixture()
def authenticated_client(api_client: TestClient) -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: (
        AuthenticatedUser(
            subject="keycloak-user-id",
            username="shawn",
            email="shawn@example.com",
            email_verified=True,
            display_name="Shawn Matunda",
            realm_roles=frozenset({"fan"}),
        )
    )

    return api_client


def test_get_my_profile_creates_and_returns_the_profile(
    authenticated_client: TestClient,
) -> None:
    response = authenticated_client.get("/api/v1/users/me")

    assert response.status_code == 200
    assert response.json()["username"] == "shawn"
    assert response.json()["roles"] == ["fan"]
    assert response.json()["profile_id"]


def test_patch_my_profile_updates_display_name(
    authenticated_client: TestClient,
) -> None:
    response = authenticated_client.patch(
        "/api/v1/users/me",
        json={"display_name": "Race Engineer"},
    )

    assert response.status_code == 200
    assert response.json()["display_name"] == "Race Engineer"


def test_profile_requires_a_bearer_token(
    api_client: TestClient,
) -> None:
    response = api_client.get("/api/v1/users/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "HTTP_401"