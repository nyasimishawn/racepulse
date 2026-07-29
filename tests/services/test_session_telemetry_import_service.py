from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models.driver import Driver
from app.models.lap import Lap
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.telemetry_point import TelemetryPoint
from app.schemas.session_telemetry_import import (
    SessionTelemetryImportCreate,
)
from app.services.session_telemetry_import_service import (
    SessionTelemetryImportService,
    SessionTelemetryImportStateError,
)
from app.services.telemetry_service import (
    TelemetryUnavailableError,
)


class FakeFastF1Provider:
    def __init__(self) -> None:
        self.load_calls = 0

    def load_telemetry_session(self, **kwargs):
        self.load_calls += 1
        return object()


class FakeLapTelemetryImporter:
    def __init__(
        self,
        db: Session,
        unavailable_laps: set[int] | None = None,
    ) -> None:
        self.db = db
        self.fastf1_provider = FakeFastF1Provider()
        self.unavailable_laps = unavailable_laps or set()

    def import_loaded_lap_telemetry(
        self,
        *,
        lap: Lap,
        driver: Driver,
        race_session: RaceSession,
        fastf1_session,
    ) -> int:
        if lap.lap_number in self.unavailable_laps:
            raise TelemetryUnavailableError(
                "FastF1 has no telemetry for this driver lap."
            )

        self.db.add(
            TelemetryPoint(
                race_session_id=race_session.id,
                driver_id=driver.id,
                lap_id=lap.id,
                sample_index=0,
                relative_time_ms=0,
                speed_kph=Decimal("250"),
                throttle_percentage=Decimal("100"),
                brake_applied=False,
                distance_m=Decimal("0"),
            )
        )
        self.db.commit()

        return 1


def seed_session(db_session: Session) -> RaceSession:
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

    hamilton = Driver(
        source="TEST",
        source_identifier="driver-44",
        driver_number="44",
        abbreviation="HAM",
        full_name="Lewis Hamilton",
    )
    verstappen = Driver(
        source="TEST",
        source_identifier="driver-1",
        driver_number="1",
        abbreviation="VER",
        full_name="Max Verstappen",
    )

    db_session.add_all([race_session, hamilton, verstappen])
    db_session.flush()

    db_session.add_all(
        [
            SessionResult(
                race_session_id=race_session.id,
                driver_id=hamilton.id,
            ),
            SessionResult(
                race_session_id=race_session.id,
                driver_id=verstappen.id,
            ),
        ]
    )

    for driver in [hamilton, verstappen]:
        for lap_number, compound, stint in [
            (1, "SOFT", 1),
            (2, "SOFT", 1),
            (3, "SOFT", 1),
            (4, "MEDIUM", 2),
            (5, "MEDIUM", 2),
            (6, "MEDIUM", 2),
        ]:
            db_session.add(
                Lap(
                    race_session_id=race_session.id,
                    driver_id=driver.id,
                    lap_number=lap_number,
                    lap_time_ms=100_000 + lap_number,
                    compound=compound,
                    stint=stint,
                    track_status="1",
                    is_accurate=True,
                    deleted=False,
                    fastf1_generated=False,
                )
            )

    db_session.commit()
    return race_session


def test_create_selects_bounded_windows_from_each_stint(
    db_session: Session,
) -> None:
    race_session = seed_session(db_session)

    service = SessionTelemetryImportService(
        db_session,
        FakeLapTelemetryImporter(db_session),
    )

    telemetry_import = service.create(
        race_session_id=race_session.id,
        payload=SessionTelemetryImportCreate(
            driver_numbers=["44"],
            max_laps_per_driver=4,
        ),
    )

    driver_result = telemetry_import.driver_results[0]

    assert driver_result["driver_number"] == "44"
    assert driver_result["selected_lap_numbers"] == [1, 2, 4, 5]
    assert telemetry_import.laps_requested == 4


def test_run_records_partial_fastf1_coverage(
    db_session: Session,
) -> None:
    race_session = seed_session(db_session)
    importer = FakeLapTelemetryImporter(
        db_session,
        unavailable_laps={2},
    )

    service = SessionTelemetryImportService(
        db_session,
        importer,
    )

    telemetry_import = service.create(
        race_session_id=race_session.id,
        payload=SessionTelemetryImportCreate(
            driver_numbers=["44"],
            max_laps_per_driver=3,
        ),
    )

    completed_import = service.run(telemetry_import.id)
    driver_result = service.list_drivers(
        telemetry_import.id
    )[0]

    assert completed_import.status.value == "PARTIAL"
    assert driver_result.status.value == "PARTIAL"
    assert driver_result.laps_with_telemetry == 2
    assert driver_result.unavailable_lap_numbers == [2]
    assert importer.fastf1_provider.load_calls == 1

    coverage = service.get_coverage(race_session.id)
    hamilton_coverage = next(
        driver
        for driver in coverage.drivers
        if driver.driver_number == "44"
    )

    assert hamilton_coverage.telemetry_lap_count == 2
    assert hamilton_coverage.telemetry_point_count == 2


def test_create_rejects_unknown_session_driver(
    db_session: Session,
) -> None:
    race_session = seed_session(db_session)
    service = SessionTelemetryImportService(
        db_session,
        FakeLapTelemetryImporter(db_session),
    )

    with pytest.raises(SessionTelemetryImportStateError):
        service.create(
            race_session_id=race_session.id,
            payload=SessionTelemetryImportCreate(
                driver_numbers=["999"],
            ),
        )