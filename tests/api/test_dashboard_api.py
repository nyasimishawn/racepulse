from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from app.models.driver import Driver
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.team import Team


def seed_dashboard(db):
    teams = [Team(source="TEST", name=name) for name in ["Alpha", "Beta"]]
    drivers = [
        Driver(
            source="TEST",
            driver_number=str(i + 1),
            full_name=f"Driver {i + 1}",
        )
        for i in range(10)
    ]
    db.add_all([*teams, *drivers])
    db.flush()
    meetings = []
    for year, month, location, grid in [
        (2023, 5, "Harbor", 1),
        (2024, 5, "Harbor", 3),
        (2026, 3, "Forest", 1),
        (2026, 4, "Forest", 1),
        (2026, 5, "Harbor", None),
        (2027, 5, "Harbor", 1),
    ]:
        meeting = Meeting(
            source="TEST",
            year=year,
            name=f"Race {year}-{month}",
            location=location,
            country_name="Example",
            event_date=datetime(year, month, 1, tzinfo=UTC),
        )
        db.add(meeting)
        db.flush()
        meetings.append(meeting)
        for identifier in ["R", "Q"]:
            session = RaceSession(
                meeting_id=meeting.id,
                name=identifier,
                session_identifier=identifier,
                session_type=identifier,
            )
            db.add(session)
            db.flush()
            for i, driver in enumerate(drivers):
                db.add(
                    SessionResult(
                        race_session_id=session.id,
                        driver_id=driver.id,
                        team_id=teams[i % 2].id,
                        position=i + 1,
                        grid_position=grid if i == 0 else i + 1,
                        points=Decimal(25 - i) if identifier == "R" else None,
                    )
                )
    db.commit()
    return meetings, drivers, teams


def test_track_history_and_forecast_are_sourced_and_time_bounded(
    api_client, db_session
):
    meetings, drivers, _ = seed_dashboard(db_session)
    path = f"/api/v1/dashboard/tracks/{meetings[0].id}?season_year=2026"
    response = api_client.get(path)
    assert response.status_code == 200, response.text
    data = response.json()
    driver = next(
        row
        for row in data["drivers"]
        if row["driver_id"] == str(drivers[0].id)
    )
    assert driver["wins_from_pole"] == 1
    assert driver["wins_outside_pole"] == 1
    assert driver["wins_with_unknown_grid"] == 1
    assert driver["qualifying_poles"] == 3
    assert driver["podiums"] == 3
    assert driver["points"] == 75
    assert driver["season_points"] == 75
    assert driver["teammate_qualifying_advantage"] > 0
    assert data["forecast_available"] is True
    assert (
        sum(t["estimated_win_percent"] for t in data["team_estimates"]) == 100
    )
    assert data["team_estimates"][0]["name"] == "Alpha"
    assert "UNCALIBRATED_ESTIMATES" in data["data_quality_flags"]
    assert data["track_race_count"] == 3  # The 2027 race cannot leak in.

    historical = api_client.get(path + "&as_of=2026-01-01T00:00:00Z").json()
    assert historical["season_race_count"] == 0
    assert historical["forecast_available"] is False
    assert historical["track_race_count"] == 2
    assert historical["team_estimates"] == []


def test_catalog_missing_data_and_validation(api_client, db_session):
    assert api_client.get("/api/v1/dashboard/tracks").json() == []
    meetings, _, _ = seed_dashboard(db_session)
    tracks = api_client.get("/api/v1/dashboard/tracks").json()
    assert len(tracks) == 2
    assert {t["name"] for t in tracks} == {"Harbor", "Forest"}
    path = f"/api/v1/dashboard/tracks/{meetings[0].id}"
    assert api_client.get(path + "?season_year=1900").status_code == 422
    assert (
        api_client.get(path + "?season_year=2026&as_of=2026-01-01").status_code
        == 422
    )
    assert (
        api_client.get(
            f"/api/v1/dashboard/tracks/{uuid4()}?season_year=2026"
        ).status_code
        == 404
    )


def test_missing_points_disable_forecast_and_are_not_claimed_complete(
    api_client, db_session
):
    meetings, _, _ = seed_dashboard(db_session)
    result = (
        db_session.query(SessionResult)
        .filter(SessionResult.points == 25)
        .first()
    )
    result.points = None
    # Remove a current-season point as well.
    current = (
        db_session.query(SessionResult)
        .join(RaceSession)
        .filter(
            RaceSession.meeting_id == meetings[2].id,
            RaceSession.session_identifier == "R",
        )
        .first()
    )
    current.points = None
    db_session.commit()
    data = api_client.get(
        f"/api/v1/dashboard/tracks/{meetings[0].id}?season_year=2026"
    ).json()
    assert data["forecast_available"] is False
    assert all(
        t["estimated_win_percent"] is None for t in data["team_estimates"]
    )
    assert "TRACK_POINTS_PARTIAL" in data["data_quality_flags"]
