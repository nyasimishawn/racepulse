from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.security import AuthenticatedUser, get_current_user
from app.main import app


def test_session_telemetry_import_routes_validate_input(
    api_client: TestClient,
) -> None:
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        subject="telemetry-editor",
        username="telemetry-editor",
        email=None,
        email_verified=False,
        display_name="Telemetry Editor",
        realm_roles=frozenset({"editor"}),
    )

    response = api_client.post(
        f"/api/v1/sessions/{uuid4()}/telemetry-imports",
        json={"max_laps_per_driver": 0},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == (
        "REQUEST_VALIDATION_FAILED"
    )

    response = api_client.get(
        "/api/v1/telemetry-imports/not-a-uuid"
    )

    assert response.status_code == 422
