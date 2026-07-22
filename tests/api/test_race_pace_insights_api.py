from uuid import uuid4

from fastapi.testclient import TestClient

from app.schemas.insight import RacePaceInsightResponse
from app.services.insight_service import (
    InsightSessionNotFoundError,
    NonRaceInsightSessionError,
    RaceInsightService,
)


def successful_response(
    race_session_id,
) -> RacePaceInsightResponse:
    return RacePaceInsightResponse(
        metric_version="race-pace-v1",
        race_session_id=race_session_id,
        session_name="Test Race",
        session_type="Race",
        data_source="LAP_DERIVED_FASTF1",
        driver_count=0,
        clean_lap_definition=[
            "lap_time_ms is present",
            "track_status is exactly '1'",
        ],
        session_benchmarks={
            "session_best_clean_lap_time_ms": None,
            "session_best_sector_1_time_ms": None,
            "session_best_sector_2_time_ms": None,
            "session_best_sector_3_time_ms": None,
            "drivers_with_clean_laps": 0,
        },
        drivers=[],
        disclaimer="Test-only response.",
    )


def test_health_endpoint_returns_api_status(
    api_client: TestClient,
) -> None:
    response = api_client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_cors_allows_local_flutter_web_origin(
    api_client: TestClient,
) -> None:
    origin = "http://localhost:54321"

    response = api_client.options(
        "/api/v1/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


def test_race_pace_endpoint_returns_success_response(
    api_client: TestClient,
    monkeypatch,
) -> None:
    race_session_id = uuid4()

    def fake_get_race_pace_insights(
        self,
        *,
        race_session_id,
        driver_number,
        include_lap_series,
    ) -> RacePaceInsightResponse:
        assert driver_number == "44"
        assert include_lap_series is True

        return successful_response(race_session_id)

    monkeypatch.setattr(
        RaceInsightService,
        "get_race_pace_insights",
        fake_get_race_pace_insights,
    )

    response = api_client.get(
        f"/api/v1/sessions/{race_session_id}/insights/race-pace",
        params={
            "driver_number": "44",
            "include_lap_series": "true",
        },
    )

    assert response.status_code == 200
    assert response.json()["session_name"] == "Test Race"


def test_race_pace_endpoint_returns_standard_404_error(
    api_client: TestClient,
    monkeypatch,
) -> None:
    race_session_id = uuid4()

    def fake_get_race_pace_insights(
        self,
        *,
        race_session_id,
        driver_number,
        include_lap_series,
    ):
        raise InsightSessionNotFoundError("Race session not found.")

    monkeypatch.setattr(
        RaceInsightService,
        "get_race_pace_insights",
        fake_get_race_pace_insights,
    )

    response = api_client.get(
        f"/api/v1/sessions/{race_session_id}/insights/race-pace"
    )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "HTTP_404",
            "message": "Race session not found.",
            "details": None,
        }
    }


def test_race_pace_endpoint_returns_standard_422_error(
    api_client: TestClient,
    monkeypatch,
) -> None:
    race_session_id = uuid4()

    def fake_get_race_pace_insights(
        self,
        *,
        race_session_id,
        driver_number,
        include_lap_series,
    ):
        raise NonRaceInsightSessionError(
            "Race Pace Insights currently support Race sessions only."
        )

    monkeypatch.setattr(
        RaceInsightService,
        "get_race_pace_insights",
        fake_get_race_pace_insights,
    )

    response = api_client.get(
        f"/api/v1/sessions/{race_session_id}/insights/race-pace"
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "HTTP_422"


def test_race_pace_endpoint_validates_boolean_query_values(
    api_client: TestClient,
) -> None:
    response = api_client.get(
        f"/api/v1/sessions/{uuid4()}/insights/race-pace",
        params={"include_lap_series": "not-a-boolean"},
    )

    assert response.status_code == 422

    body = response.json()

    assert body["error"]["code"] == "REQUEST_VALIDATION_FAILED"
    assert body["error"]["details"][0]["location"] == (
        "query.include_lap_series"
    )