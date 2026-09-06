from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pandas as pd
import pytest
from sqlalchemy import func, select

from app.models.durable_job import DurableJob, DurableJobStatus
from app.models.lap import Lap
from app.models.race_control_event import RaceControlEvent
from app.models.race_session import RaceSession
from app.models.session_map_sample import SessionMapSample
from app.models.session_telemetry_import import SessionTelemetryImport
from app.models.telemetry_point import TelemetryPoint
from app.models.weather_sample import WeatherSample
from app.models.weekend_download import WeekendDownload
from app.providers.fastf1_provider import FastF1Provider
from app.schemas.weekend import (
    WeekendSchedule,
    WeekendSelectionRequest,
    WeekendSessionSchedule,
)
from app.services.durable_job_service import (
    DurableJobService,
    JobCancellationRequested,
)
from app.services.weekend_download_service import (
    WeekendDownloadError,
    WeekendDownloadService,
)


class LoadedLaps(pd.DataFrame):
    @property
    def _constructor(self):
        return LoadedLaps

    def pick_drivers(self, number):
        return self[self["DriverNumber"] == number]

    def pick_laps(self, number):
        return self[self["LapNumber"] == number]

    def get_telemetry(self, frequency="original"):
        assert frequency == "original"
        lap = self.iloc[0]
        times = [pd.Timedelta(i * 100, unit="ms") for i in range(10)]
        return pd.DataFrame(
            {
                "Time": times,
                "SessionTime": [lap["LapStartTime"] + time for time in times],
                "Date": [lap["LapStartDate"] + time for time in times],
                "Speed": [250] * 10,
                "Throttle": [100] * 10,
                "Brake": [False] * 10,
                "Distance": list(range(10)),
                "X": list(range(10)),
                "Y": list(range(10)),
                "Source": ["car"] * 10,
            }
        )

    def get_pos_data(self):
        # Two samples 10ms apart in every 1000ms block prove that raw imports
        # retain more samples than the legacy 250ms buckets.
        times = [
            pd.Timedelta(i * 1000 + offset, unit="ms")
            for i in range(120)
            for offset in (0, 10)
        ]
        return pd.DataFrame(
            {
                "SessionTime": times,
                "X": list(range(240)),
                "Y": list(range(240)),
                "Z": [1] * 240,
                "Status": ["OnTrack"] * 240,
                "Source": ["pos"] * 240,
            }
        )


class FakeWeekendProvider:
    def __init__(self, *, sprint=False):
        self.schedule_calls = 0
        self.loads = []
        self.fail_once = set()
        names = [
            ("FP1", "Practice 1"),
            ("FP2", "Practice 2"),
            ("FP3", "Practice 3"),
            ("Q", "Qualifying"),
            ("R", "Race"),
        ]
        if sprint:
            names = [
                ("FP1", "Practice 1"),
                ("Q", "Qualifying"),
                ("SS", "Sprint Shootout"),
                ("S", "Sprint"),
                ("R", "Race"),
            ]
        self.schedule = WeekendSchedule(
            year=2023,
            round_number=14,
            event_name="Italian Grand Prix",
            aliases=["monza", "italian grand prix", "italy", "14"],
            sessions=[
                WeekendSessionSchedule(
                    identifier=identifier,
                    name=name,
                    scheduled_at=datetime(2023, 9, 1, 10, tzinfo=UTC),
                )
                for identifier, name in names
            ],
        )

    def get_weekend_schedule(self, **kwargs):
        self.schedule_calls += 1
        return self.schedule

    def load_full_session(self, *, year, round_number, session_identifier):
        self.loads.append(session_identifier)
        if session_identifier in self.fail_once:
            self.fail_once.remove(session_identifier)
            raise RuntimeError("temporary provider outage")
        name = next(
            s.name
            for s in self.schedule.sessions
            if s.identifier == session_identifier
        )
        date = pd.Timestamp("2023-09-01T10:00:00Z")
        results = pd.DataFrame(
            [
                {
                    "DriverNumber": number,
                    "DriverId": f"driver-{number}",
                    "FullName": f"Driver {number}",
                    "Abbreviation": f"D{number}",
                    "TeamName": "Test Team",
                    "TeamId": "test-team",
                    "Position": index,
                    "Status": "Finished",
                    "Q1": pd.Timedelta(61, unit="s"),
                    "Q2": pd.Timedelta(60, unit="s"),
                    "Q3": pd.Timedelta(59, unit="s"),
                }
                for index, number in enumerate(("1", "2"), 1)
            ]
        )
        laps = LoadedLaps(
            [
                {
                    "DriverNumber": number,
                    "LapNumber": lap,
                    "LapTime": pd.Timedelta(60, unit="s"),
                    "LapStartTime": pd.Timedelta((lap - 1) * 60, unit="s"),
                    "LapStartDate": date
                    + pd.Timedelta((lap - 1) * 60, unit="s"),
                    "Compound": "SOFT",
                    "Stint": 1,
                    "TyreLife": lap,
                    "IsAccurate": True,
                    "TrackStatus": "1",
                    "Deleted": lap
                    == 2,  # Full downloads also retain deleted laps.
                }
                for number in ("1", "2")
                for lap in (1, 2)
            ]
        )
        circuit = SimpleNamespace(
            corners=pd.DataFrame([{"Number": 1, "X": 1, "Y": 2}]),
            marshal_lights=pd.DataFrame(),
            marshal_sectors=pd.DataFrame(),
            rotation=0.0,
        )
        return SimpleNamespace(
            event={
                "EventName": "Italian Grand Prix",
                "Location": "Monza",
                "Country": "Italy",
            },
            date=date,
            name=name,
            results=results,
            laps=laps,
            weather_data=pd.DataFrame(
                [{"Time": pd.Timedelta(0), "AirTemp": 25}]
            ),
            race_control_messages=pd.DataFrame(
                [{"Time": date, "Message": "GREEN LIGHT"}]
            ),
            session_info={"Name": name},
            session_status=pd.DataFrame(
                [{"Time": pd.Timedelta(0), "Status": "Started"}]
            ),
            track_status=pd.DataFrame(
                [{"Time": pd.Timedelta(0), "Status": "1"}]
            ),
            get_circuit_info=lambda: circuit,
        )


@pytest.fixture(autouse=True)
def no_provider_cache_side_effects(monkeypatch):
    monkeypatch.setattr(FastF1Provider, "__init__", lambda self: None)


def selected_service(db_session, provider=None, **kwargs):
    provider = provider or FakeWeekendProvider()
    service = WeekendDownloadService(db_session, provider=provider, **kwargs)
    selection = service.select_weekend(
        WeekendSelectionRequest(year=2023, event_name="Monza")
    )
    return service, provider, selection


def test_complete_weekend_is_loaded_once_and_every_data_stage_is_stored(
    db_session,
):
    service, provider, selected = selected_service(db_session)
    queue = DurableJobService(db_session)
    queue.claim(selected.job.id, lease_owner="test-worker")
    result = service.run(selected.id)
    queue.complete(selected.job.id, result=result, lease_owner="test-worker")

    assert result["coverage"] == "COMPLETE", service.get(selected.id).sessions
    assert provider.loads == ["FP1", "FP2", "FP3", "Q", "R"]
    for model, expected in (
        (RaceSession, 5),
        (Lap, 20),
        (TelemetryPoint, 200),
        (SessionMapSample, 2400),
        (WeatherSample, 5),
        (RaceControlEvent, 5),
    ):
        assert (
            db_session.scalar(select(func.count()).select_from(model))
            == expected
        )
    imports = db_session.scalars(select(SessionTelemetryImport)).all()
    assert all(
        item.max_laps_per_driver is None and not item.clean_laps_only
        for item in imports
    )
    assert all(item.laps_with_telemetry == 4 for item in imports)
    assert all(
        session.source_metadata["circuit"]["corners"]
        for session in db_session.scalars(select(RaceSession)).all()
    )

    from app.services.replay_room_plan_service import ReplayRoomPlanService

    enrichment = ReplayRoomPlanService(db_session).build_enrichment(
        race_session_id=UUID(
            service.get(selected.id).sessions[0]["race_session_id"]
        ),
        source_time_origin_ms=0,
        timing_duration_ms=120_000,
        eligible_driver_count=2,
    )
    assert enrichment.map_metadata["track_map_available"] is True
    assert enrichment.map_metadata["map_sample_interval_ms"] == 250

    cached = service.select_weekend(
        WeekendSelectionRequest(year=2023, event_name="Italian Grand Prix")
    )
    assert (
        cached.id == selected.id and cached.cached and cached.status == "READY"
    )
    assert provider.schedule_calls == 1 and len(provider.loads) == 5
    assert db_session.scalar(select(func.count()).select_from(DurableJob)) == 1


def test_sprint_schedule_uses_only_sessions_that_exist(db_session):
    service, provider, selected = selected_service(
        db_session, FakeWeekendProvider(sprint=True)
    )
    service.run(selected.id)
    assert provider.loads == ["FP1", "Q", "SS", "S", "R"]
    assert "FP2" not in provider.loads and "FP3" not in provider.loads


def test_partial_provider_outage_retries_only_failed_session(db_session):
    service, provider, selected = selected_service(db_session)
    provider.fail_once.add("Q")
    with pytest.raises(WeekendDownloadError):
        service.run(selected.id)
    assert (
        db_session.scalar(select(func.count()).select_from(RaceSession)) == 4
    )
    result = service.run(selected.id)
    assert result["coverage"] == "COMPLETE"
    assert provider.loads == ["FP1", "FP2", "FP3", "Q", "R", "Q"]
    assert db_session.scalar(select(func.count()).select_from(Lap)) == 20


def test_interrupted_stage_resumes_without_duplicate_laps(db_session):
    service, provider, selected = selected_service(db_session)
    original = service._run_stage
    interrupted = False

    def interrupt_after_results(name, *args, **kwargs):
        nonlocal interrupted
        if name == "laps" and not interrupted:
            interrupted = True
            raise JobCancellationRequested("interrupt fixture")
        return original(name, *args, **kwargs)

    service._run_stage = interrupt_after_results
    with pytest.raises(JobCancellationRequested):
        service.run(selected.id)
    assert (
        service.get(selected.id).sessions[0]["stages"]["results"]["status"]
        == "COMPLETED"
    )
    result = service.run(selected.id)
    assert result["coverage"] == "COMPLETE"
    assert db_session.scalar(select(func.count()).select_from(Lap)) == 20


def test_future_session_is_reported_and_selected_again_when_due(db_session):
    provider = FakeWeekendProvider()
    provider.schedule.sessions = provider.schedule.sessions[:1]
    provider.schedule.sessions[0].scheduled_at = datetime.now(UTC) + timedelta(
        days=1
    )
    service, provider, selected = selected_service(db_session, provider)
    queue = DurableJobService(db_session)
    queue.claim(selected.job.id, lease_owner="test-worker")
    result = service.run(selected.id)
    queue.complete(selected.job.id, result=result)
    assert result["coverage"] == "PARTIAL" and provider.loads == []
    weekend = service.get(selected.id)
    sessions = deepcopy(weekend.sessions)
    sessions[0]["scheduled_at"] = (
        datetime.now(UTC) - timedelta(days=1)
    ).isoformat()
    weekend.sessions = sessions
    db_session.commit()
    refreshed = service.select_weekend(
        WeekendSelectionRequest(year=2023, event_name="Monza")
    )
    assert refreshed.job.id != selected.job.id
    assert refreshed.job.status == DurableJobStatus.QUEUED
    assert provider.schedule_calls == 1


def test_repeated_selection_shares_one_job(db_session):
    service, provider, first = selected_service(db_session)
    second = service.select_weekend(
        WeekendSelectionRequest(year=2023, event_name="MONZA")
    )
    assert first.id == second.id and first.job.id == second.job.id
    assert (
        db_session.scalar(select(func.count()).select_from(WeekendDownload))
        == 1
    )
    assert provider.loads == []


def test_full_telemetry_has_no_25_lap_cap(db_session):
    service, _, selected = selected_service(db_session)
    service.run(selected.id)
    session_id = UUID(service.get(selected.id).sessions[0]["race_session_id"])
    first = db_session.scalar(
        select(Lap).where(Lap.race_session_id == session_id)
    )
    for number in range(3, 33):
        db_session.add(
            Lap(
                race_session_id=session_id,
                driver_id=first.driver_id,
                lap_number=number,
            )
        )
    db_session.commit()
    from app.schemas.session_telemetry_import import (
        SessionTelemetryImportCreate,
    )
    from app.services.session_telemetry_import_service import (
        SessionTelemetryImportService,
    )

    imported = SessionTelemetryImportService(db_session).create(
        race_session_id=session_id,
        payload=SessionTelemetryImportCreate(
            max_laps_per_driver=None, clean_laps_only=False
        ),
    )
    assert (
        max(
            len(item["selected_lap_numbers"])
            for item in imported.driver_results
        )
        == 32
    )


def test_worker_executes_weekend_and_persists_its_result(
    db_session, monkeypatch
):
    from sqlalchemy.orm import sessionmaker
    from app.workers.job_worker import DurableJobWorker

    _, provider, selected = selected_service(db_session)
    monkeypatch.setattr(
        "app.workers.job_worker.SessionLocal",
        sessionmaker(
            bind=db_session.get_bind(),
            autoflush=False,
        ),
    )
    monkeypatch.setattr(
        "app.services.weekend_download_service.FastF1Provider",
        lambda: provider,
    )
    worker = DurableJobWorker(SimpleNamespace(), worker_id="worker-fixture")
    worker._process_message({"job_id": str(selected.job.id)})
    db_session.expire_all()
    job = db_session.get(DurableJob, selected.job.id)
    assert job.status == DurableJobStatus.COMPLETED, job.failure_reason
    assert job.result["coverage"] == "COMPLETE"
    assert job.progress_percentage == 100
    assert len(provider.loads) == 5


def test_partial_lap_failure_is_retried_without_redownloading_other_sessions(
    db_session, monkeypatch
):
    from app.services.telemetry_service import (
        TelemetryImportError,
        TelemetryImportService,
    )

    service, provider, selected = selected_service(db_session)
    original = TelemetryImportService.import_loaded_lap_telemetry
    fail = True

    def fail_one_lap(self, **kwargs):
        nonlocal fail
        if fail:
            fail = False
            raise TelemetryImportError("temporary import failure")
        return original(self, **kwargs)

    monkeypatch.setattr(
        TelemetryImportService, "import_loaded_lap_telemetry", fail_one_lap
    )
    with pytest.raises(WeekendDownloadError):
        service.run(selected.id)
    assert service.run(selected.id)["coverage"] == "COMPLETE"
    assert provider.loads == ["FP1", "FP2", "FP3", "Q", "R", "FP1"]
    assert (
        db_session.scalar(select(func.count()).select_from(TelemetryPoint))
        == 200
    )


def test_unavailable_telemetry_is_visible_without_blocking_other_data(
    db_session, monkeypatch
):
    from app.services.telemetry_service import (
        TelemetryUnavailableError,
        TelemetryImportService,
    )

    service, provider, selected = selected_service(db_session)

    def unavailable(self, **kwargs):
        raise TelemetryUnavailableError("provider has no trace")

    monkeypatch.setattr(
        TelemetryImportService, "import_loaded_lap_telemetry", unavailable
    )
    result = service.run(selected.id)
    assert result["coverage"] == "PARTIAL"
    weekend = service.get(selected.id)
    assert all(
        s["stages"]["telemetry"]["details"]["laps_unavailable"] == 4
        for s in weekend.sessions
    )
    assert all(
        s["stages"]["map"]["status"] == "COMPLETED" for s in weekend.sessions
    )
    assert all(
        s["stages"]["context"]["status"] == "COMPLETED"
        for s in weekend.sessions
    )
