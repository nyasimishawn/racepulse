from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.driver import Driver
from app.models.lap import Lap
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.telemetry_point import TelemetryPoint
from app.services.tyre_insight_service import RaceEngineeringInsightService


def seed_driver_laps(db_session: Session) -> tuple[RaceSession, Driver]:
    meeting = Meeting(
        source="TEST",
        year=2023,
        name="Test Grand Prix",
    )
    db_session.add(meeting)
    db_session.flush()

    race_session = RaceSession(
        meeting_id=meeting.id,
        name="Race",
        session_identifier="R",
        session_type="Race",
    )
    driver = Driver(
        source="TEST",
        source_identifier="driver-44",
        driver_number="44",
        abbreviation="HAM",
        full_name="Lewis Hamilton",
    )
    db_session.add_all([race_session, driver])
    db_session.flush()

    lap_specs = [
        (1, 100_000, "SOFT", 1, 1),
        (2, 100_240, "SOFT", 2, 1),
        (3, 100_500, "SOFT", 3, 1),
        (4, 101_000, "MEDIUM", 1, 2),
        (5, 101_220, "MEDIUM", 2, 2),
        (6, 101_520, "MEDIUM", 3, 2),
    ]
    for lap_number, lap_time_ms, compound, tyre_life, stint in lap_specs:
        db_session.add(
            Lap(
                race_session_id=race_session.id,
                driver_id=driver.id,
                lap_number=lap_number,
                lap_time_ms=lap_time_ms,
                stint=stint,
                compound=compound,
                tyre_life=Decimal(tyre_life),
                track_status="1",
                is_accurate=True,
                deleted=False,
                fastf1_generated=False,
            )
        )

    db_session.flush()
    return race_session, driver


def test_tyre_insights_score_laps_against_same_compound_baseline(
    db_session: Session,
) -> None:
    race_session, _ = seed_driver_laps(db_session)

    response = RaceEngineeringInsightService(
        db_session
    ).get_tyre_insights(
        race_session_id=race_session.id,
        driver_number="44",
    )

    assert response.metric_version == "race-engineering-tyre-v1"
    assert [baseline.compound for baseline in response.compound_baselines] == [
        "MEDIUM",
        "SOFT",
    ]

    medium_lap = next(
        lap_score
        for lap_score in response.lap_scores
        if lap_score.lap_number == 4
    )

    assert medium_lap.baseline_ms == 101_220.0
    assert medium_lap.delta_to_compound_baseline_ms == -220.0
    assert medium_lap.classification == "FASTER_THAN_BASELINE"
    assert "same driver's same-compound" in response.disclaimer


def test_lift_and_coast_service_marks_missing_telemetry(
    db_session: Session,
) -> None:
    race_session, _ = seed_driver_laps(db_session)

    response = RaceEngineeringInsightService(
        db_session
    ).get_lift_and_coast_insights(
        race_session_id=race_session.id,
        driver_number="44",
    )

    assert response.lap_summaries == []
    assert "TELEMETRY_NOT_IMPORTED" in response.data_quality_flags


def test_lift_and_coast_service_uses_stored_telemetry(
    db_session: Session,
) -> None:
    race_session, driver = seed_driver_laps(db_session)
    lap_four = (
        db_session.query(Lap)
        .filter(
            Lap.race_session_id == race_session.id,
            Lap.driver_id == driver.id,
            Lap.lap_number == 4,
        )
        .one()
    )

    for index, (distance, throttle, brake) in enumerate(
        [
            (700, 100, False),
            (760, 100, False),
            (820, 20, False),
            (880, 15, False),
            (940, 10, False),
            (1000, 0, True),
            (1040, 0, True),
            (1120, 45, False),
        ]
    ):
        db_session.add(
            TelemetryPoint(
                race_session_id=race_session.id,
                driver_id=driver.id,
                lap_id=lap_four.id,
                sample_index=index,
                relative_time_ms=index * 100,
                speed_kph=Decimal(310 - index * 5),
                throttle_percentage=Decimal(throttle),
                brake_applied=brake,
                distance_m=Decimal(distance),
            )
        )
    db_session.flush()

    response = RaceEngineeringInsightService(
        db_session
    ).get_lift_and_coast_insights(
        race_session_id=race_session.id,
        driver_number="44",
        start_lap=4,
        end_lap=4,
    )

    assert response.lap_summaries[0].classification == (
        "INFERRED_LIFT_AND_COAST"
    )
    assert response.zone_observations[0].lift_distance_before_brake_m == 180.0
    assert response.persistence_lap_ranges == []
