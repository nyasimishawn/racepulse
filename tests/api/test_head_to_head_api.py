from uuid import uuid4

from fastapi.testclient import TestClient


def test_head_to_head_route_validates_driver_query_values(
    api_client: TestClient,
) -> None:
    response = api_client.get(
        f"/api/v1/sessions/{uuid4()}/head-to-head"
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == (
        "REQUEST_VALIDATION_FAILED"
    )