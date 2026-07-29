from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.driver import Driver
from app.models.lap import Lap
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.services.head_to_head_service import HeadToHeadService


def test_head_to_head_compares_two_race_drivers(
    db_session: Session,
) -> None:
    meeting = Meeting(
        source="TEST",
        year=2023,
        name="Test Grand Prix",
    )
    db_session.add(meeting)
    db_session.flush()

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
    hamilton = Driver(
        source="TEST",
        source_identifier="hamilton",
        driver_number="44",
        abbreviation="HAM",
        full_name="Lewis Hamilton",
    )
    verstappen = Driver(
        source="TEST",
        source_identifier="verstappen",
        driver_number="1",
        abbreviation="VER",
        full_name="Max Verstappen",
    )
    db_session.add_all([race, qualifying, hamilton, verstappen])
    db_session.flush()

    db_session.add_all(
        [
            SessionResult(
                race_session_id=race.id,
                driver_id=hamilton.id,
                position=1,
                classified_position="1",
                points=Decimal("25"),
            ),
            SessionResult(
                race_session_id=race.id,
                driver_id=verstappen.id,
                position=2,
                classified_position="2",
                points=Decimal("18"),
            ),
            SessionResult(
                race_session_id=qualifying.id,
                driver_id=hamilton.id,
                position=1,
                q3_time_ms=95_000,
            ),
            SessionResult(
                race_session_id=qualifying.id,
                driver_id=verstappen.id,
                position=2,
                q3_time_ms=95_100,
            ),
        ]
    )

    for driver, lap_times in [
        (hamilton, [100_000, 100_100, 100_200, 100_300]),
        (verstappen, [101_000, 101_100, 101_500, 103_000]),
    ]:
        for lap_number, lap_time_ms in enumerate(lap_times, start=1):
            db_session.add(
                Lap(
                    race_session_id=race.id,
                    driver_id=driver.id,
                    lap_number=lap_number,
                    lap_time_ms=lap_time_ms,
                    compound="SOFT",
                    stint=1,
                    tyre_life=Decimal(lap_number),
                    track_status="1",
                    is_accurate=True,
                    deleted=False,
                    fastf1_generated=False,
                )
            )

    db_session.commit()

    response = HeadToHeadService(db_session).compare(
        race_session_id=race.id,
        driver_a_number="44",
        driver_b_number="1",
    )

    assert response.comparison_version == "head-to-head-v1"
    assert response.driver_a.race_result.finishing_position == 1
    assert response.driver_b.qualifying.position == 2
    assert response.shared_compound_pace[0].compound == "SOFT"
    assert response.shared_compound_pace[0].faster_driver_number == "44"
    assert response.score.driver_a_points == 10
    assert response.score.driver_b_points == 0
    assert response.score.winner_driver_number == "44"