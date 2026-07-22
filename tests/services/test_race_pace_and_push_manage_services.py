from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models.driver import Driver
from app.models.lap import Lap
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.team import Team
from app.services.insight_service import (
    NonRaceInsightSessionError,
    RaceInsightService,
)
from app.services.push_manage_timeline_service import (
    PushManageTimelineService,
)


def seed_race(db_session: Session) -> tuple[RaceSession, Driver]:
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
    team = Team(
        source="TEST",
        source_identifier="team-mercedes",
        name="Mercedes",
        colour="27F4D2",
    )
    db_session.add_all([race_session, driver, team])
    db_session.flush()

    db_session.add(
        SessionResult(
            race_session_id=race_session.id,
            driver_id=driver.id,
            team_id=team.id,
            position=2,
            grid_position=5,
            status="Finished",
        )
    )

    for lap_number, lap_time_ms in enumerate(
        [100_000, 100_100, 100_200, 100_300, 100_400],
        start=1,
    ):
        db_session.add(
            Lap(
                race_session_id=race_session.id,
                driver_id=driver.id,
                lap_number=lap_number,
                lap_start_time_ms=(lap_number - 1) * 100_000,
                lap_time_ms=lap_time_ms,
                sector_1_time_ms=33_000,
                sector_2_time_ms=33_300,
                sector_3_time_ms=33_700,
                stint=1,
                compound="MEDIUM",
                tyre_life=Decimal(lap_number),
                fresh_tyre=lap_number == 1,
                track_status="1",
                is_accurate=True,
                deleted=False,
                fastf1_generated=False,
            )
        )

    # A pit-entry lap must not be treated as a clean pace lap.
    db_session.add(
        Lap(
            race_session_id=race_session.id,
            driver_id=driver.id,
            lap_number=6,
            lap_start_time_ms=500_000,
            lap_time_ms=120_000,
            sector_1_time_ms=40_000,
            sector_2_time_ms=40_000,
            sector_3_time_ms=40_000,
            stint=1,
            compound="MEDIUM",
            tyre_life=Decimal("6"),
            pit_in_time_ms=590_000,
            track_status="1",
            is_accurate=True,
            deleted=False,
            fastf1_generated=False,
        )
    )

    db_session.flush()

    return race_session, driver


def test_race_pace_service_uses_only_clean_green_laps(
    db_session: Session,
) -> None:
    race_session, _ = seed_race(db_session)

    response = RaceInsightService(
        db_session
    ).get_race_pace_insights(
        race_session_id=race_session.id,
        driver_number="44",
        include_lap_series=True,
    )

    assert response.driver_count == 1
    assert (
        response.session_benchmarks.session_best_clean_lap_time_ms
        == 100_000
    )

    driver = response.drivers[0]

    assert driver.driver_number == "44"
    assert driver.driver_name == "Lewis Hamilton"
    assert driver.positions_gained == 3
    assert driver.clean_lap_count == 5
    assert driver.fastest_clean_lap_time_ms == 100_000
    assert driver.median_clean_lap_time_ms == 100_200.0
    assert len(driver.stints) == 1

    stint = driver.stints[0]

    assert stint.raw_lap_count == 6
    assert stint.clean_lap_count == 5
    assert stint.trend_direction == "PACE_FALLING"
    assert stint.tyre_pace_proxy.classification == "PACE_LOSS_PROXY"
    assert [point.lap_number for point in stint.lap_series] == [
        1,
        2,
        3,
        4,
        5,
    ]


def test_push_manage_marks_missing_qualifying_as_not_scored(
    db_session: Session,
) -> None:
    race_session, _ = seed_race(db_session)

    response = PushManageTimelineService(
        db_session
    ).get_timeline(
        race_session_id=race_session.id,
        driver_number="44",
        start_lap=1,
        end_lap=1,
        include_context=False,
    )

    assert response.qualifying_reference.qualifying_session_id is None
    assert response.qualifying_reference.unavailable_reason == (
        "Qualifying has not been imported for this meeting."
    )
    assert response.coverage.total_requested_laps == 1
    assert response.coverage.scored_lap_count == 0
    assert response.coverage.unscored_lap_count == 1
    assert response.mode_counts == {"NOT_SCORED": 1}
    assert response.warnings == [
        "QUALIFYING_REFERENCE_UNAVAILABLE"
    ]

    entry = response.entries[0]

    assert entry.telemetry_eligible is False
    assert entry.observed_mode == "NOT_SCORED"
    assert entry.evidence_tags == ["TELEMETRY_NOT_IMPORTED"]
    assert entry.ineligibility_reasons == [
        "Qualifying has not been imported for this meeting."
    ]


def test_race_pace_service_rejects_non_race_sessions(
    db_session: Session,
) -> None:
    meeting = Meeting(
        source="TEST",
        year=2023,
        name="Test Grand Prix",
    )
    db_session.add(meeting)
    db_session.flush()

    qualifying_session = RaceSession(
        meeting_id=meeting.id,
        name="Qualifying",
        session_identifier="Q",
        session_type="Qualifying",
    )
    db_session.add(qualifying_session)
    db_session.flush()

    with pytest.raises(
        NonRaceInsightSessionError,
        match="Race sessions only",
    ):
        RaceInsightService(
            db_session
        ).get_race_pace_insights(
            race_session_id=qualifying_session.id,
            driver_number=None,
            include_lap_series=False,
        )