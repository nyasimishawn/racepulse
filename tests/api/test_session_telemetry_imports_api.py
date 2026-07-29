from uuid import uuid4

from fastapi.testclient import TestClient


def test_session_telemetry_import_routes_validate_input(
    api_client: TestClient,
) -> None:
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