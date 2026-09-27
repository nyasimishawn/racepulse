from datetime import UTC, datetime

from app.models.driver import Driver
from app.models.lap import Lap
from app.models.meeting import Meeting
from app.models.race_control_event import RaceControlEvent
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.telemetry_point import TelemetryPoint
from app.models.weather_sample import WeatherSample


def seed_pair(db):
    meeting = Meeting(source="TEST", year=2026, name="Comparison race")
    drivers = [
        Driver(source="TEST", driver_number=n, full_name=f"Driver {n}")
        for n in ["1", "2"]
    ]
    db.add_all([meeting, *drivers])
    db.flush()
    race = RaceSession(
        meeting_id=meeting.id,
        name="Race",
        session_identifier="R",
        session_type="Race",
    )
    qualifying = RaceSession(
        meeting_id=meeting.id,
        name="Qualifying",
        session_identifier="Q",
        session_type="Qualifying",
    )
    db.add_all([race, qualifying])
    db.flush()
    laps = []
    for index, driver in enumerate(drivers):
        db.add(
            SessionResult(
                race_session_id=race.id,
                driver_id=driver.id,
                position=2 - index,
                grid_position=0 if index == 0 else 5,
            )
        )
        db.add(
            SessionResult(
                race_session_id=qualifying.id,
                driver_id=driver.id,
                position=1 + index,
                q2_time_ms=92000 + index * 500,
                q3_time_ms=90000 if index == 0 else None,
            )
        )
        for number in [2, 3, 4]:
            lap = Lap(
                race_session_id=race.id,
                driver_id=driver.id,
                lap_number=number,
                lap_time_ms=95000 + index * 100,
                sector_1_time_ms=30000 + index * 100,
                sector_2_time_ms=30000 - index * 100,
                sector_3_time_ms=35000,
                compound="MEDIUM",
                is_accurate=True,
                track_status="1",
            )
            db.add(lap)
            laps.append(lap)
    db.add(
        RaceControlEvent(
            race_session_id=race.id,
            source="TEST",
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
            flag="YELLOW",
            message="Yellow flag",
        )
    )
    db.add(
        WeatherSample(
            race_session_id=race.id,
            source="TEST",
            session_time_ms=0,
            rainfall=True,
        )
    )
    db.commit()
    return race, drivers, laps


def add_telemetry(db, race, driver, lap):
    for i in range(50):
        db.add(
            TelemetryPoint(
                race_session_id=race.id,
                driver_id=driver.id,
                lap_id=lap.id,
                sample_index=i,
                relative_time_ms=i * 100,
                distance_m=i,
                speed_kph=200,
                sample_source="car",
                throttle_percentage=80,
                brake_applied=False,
            )
        )
    db.commit()


def test_shared_qualifying_segment_positions_sector_wins_and_context(
    api_client, db_session
):
    race, _, _ = seed_pair(db_session)
    path = f"/api/v1/sessions/{race.id}/head-to-head"
    response = api_client.get(path, params={"driver_a": "1", "driver_b": "2"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["qualifying_comparison"]["common_segment"] == "Q2"
    assert data["qualifying_comparison"]["driver_b_minus_driver_a_ms"] == 500
    assert data["driver_a"]["race_result"]["positions_gained"] is None
    assert data["driver_b"]["race_result"]["positions_gained"] == 4
    assert [s["winner_driver_number"] for s in data["sector_comparisons"]] == [
        "1",
        "2",
        None,
    ]
    assert data["race_context"]["rainfall_observed"] is True
    assert (
        data["race_context"]["race_control_events"][0]["message"]
        == "Yellow flag"
    )
    assert data["score"]["score_type"] == "DRIVER_COMPARISON"
    assert data["score"]["fantasy_points_awarded"] == 0
    assert (
        api_client.get(
            path, params={"driver_a": "1", "driver_b": "1"}
        ).status_code
        == 422
    )
    assert (
        api_client.get(
            path, params={"driver_a": "1", "driver_b": "99"}
        ).status_code
        == 404
    )


def test_telemetry_requires_both_drivers_and_overlay_quality(
    api_client, db_session
):
    race, drivers, laps = seed_pair(db_session)
    path = f"/api/v1/sessions/{race.id}/head-to-head?driver_a=1&driver_b=2"
    add_telemetry(db_session, race, drivers[0], laps[0])
    first = api_client.get(path).json()["telemetry_coverage"]
    assert first["both_drivers_have_coverage"] is False
    assert len(first["driver_a_laps"]) == 1
    add_telemetry(db_session, race, drivers[1], laps[3])
    assert (
        api_client.get(path).json()["telemetry_coverage"][
            "both_drivers_have_coverage"
        ]
        is True
    )
    overlay = api_client.get(
        "/api/v1/lap-comparison/telemetry-overlay",
        params={
            "reference_session_id": str(race.id),
            "target_session_id": str(race.id),
            "reference_driver_number": "1",
            "target_driver_number": "2",
            "reference_lap_number": 2,
            "target_lap_number": 2,
        },
    )
    assert overlay.status_code == 200, overlay.text
    assert (
        overlay.json()["eligible"] is False
    )  # 49 metres is not a complete lap.


def test_full_telemetry_pair_can_be_compared(api_client, db_session):
    race, drivers, laps = seed_pair(db_session)
    for driver, lap in [(drivers[0], laps[0]), (drivers[1], laps[3])]:
        for i in range(200):
            db_session.add(
                TelemetryPoint(
                    race_session_id=race.id,
                    driver_id=driver.id,
                    lap_id=lap.id,
                    sample_index=i,
                    relative_time_ms=round(lap.lap_time_ms * i / 199),
                    distance_m=i * 25,
                    speed_kph=200,
                    throttle_percentage=80,
                    brake_applied=False,
                    sample_source="car",
                )
            )
    db_session.commit()
    response = api_client.get(
        "/api/v1/lap-comparison/telemetry-overlay",
        params={
            "reference_session_id": str(race.id),
            "target_session_id": str(race.id),
            "reference_driver_number": "1",
            "target_driver_number": "2",
            "reference_lap_number": 2,
            "target_lap_number": 2,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["eligible"] is True, response.json()
    assert response.json()["coverage_percent"] >= 70
