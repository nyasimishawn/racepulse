from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import json
import logging
from typing import Callable
from uuid import UUID

import pandas as pd
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.durable_job import DurableJob, DurableJobStatus, DurableJobType
from app.models.import_job import DataSource, ImportJob
from app.models.race_session import RaceSession
from app.models.weekend_download import WeekendDownload
from app.providers.fastf1_provider import FastF1Provider
from app.schemas.durable_job import DurableJobResponse
from app.schemas.session_map_import import SessionMapImportCreate
from app.schemas.session_telemetry_import import SessionTelemetryImportCreate
from app.schemas.weekend import (
    DownloadStage,
    WeekendDownloadResponse,
    WeekendSelectionRequest,
    WeekendSessionDownload,
)
from app.services.durable_job_service import (
    DurableJobService,
    JobCancellationRequested,
    JobPublisher,
    JobStateError,
)
from app.services.lap_service import LapImportService
from app.services.race_context_service import RaceContextService
from app.services.session_import_service import SessionImportService
from app.services.session_map_import_service import SessionMapImportService
from app.services.session_telemetry_import_service import (
    SessionTelemetryImportService,
)


STAGES = ("results", "laps", "telemetry", "map", "context", "metadata")
FINISHED_STAGES = {"COMPLETED", "PARTIAL", "UNAVAILABLE"}
ACTIVE_JOBS = {
    DurableJobStatus.QUEUED,
    DurableJobStatus.RUNNING,
    DurableJobStatus.RETRY_WAIT,
    DurableJobStatus.CANCEL_REQUESTED,
}


logger = logging.getLogger(__name__)


class WeekendDownloadError(RuntimeError):
    pass


class WeekendDownloadService:
    def __init__(
        self,
        db: Session,
        *,
        provider=None,
        publisher: JobPublisher | None = None,
        cancellation_check: Callable[[], None] | None = None,
        progress_callback: Callable[[int], None] | None = None,
    ) -> None:
        self.db = db
        self._provider = provider
        self.publisher = publisher
        self._cancellation_check = cancellation_check or (lambda: None)
        self._progress_callback = progress_callback or (lambda value: None)

    @property
    def provider(self):
        if self._provider is None:
            self._provider = FastF1Provider()
        return self._provider

    def select_weekend(
        self,
        payload: WeekendSelectionRequest,
    ) -> WeekendDownloadResponse:
        query = payload.event_name.casefold()
        existing = self.db.scalars(
            select(WeekendDownload).where(WeekendDownload.year == payload.year)
        ).all()
        matches = [item for item in existing if query in item.aliases]
        weekend = matches[0] if len(matches) == 1 else None
        cached = weekend is not None
        if weekend is None:
            schedule = self.provider.get_weekend_schedule(
                year=payload.year,
                event_name=payload.event_name,
            )
            weekend = self.db.scalar(
                select(WeekendDownload).where(
                    WeekendDownload.year == schedule.year,
                    WeekendDownload.round_number == schedule.round_number,
                )
            )
            cached = weekend is not None
            if weekend is None:
                weekend = WeekendDownload(
                    year=schedule.year,
                    round_number=schedule.round_number,
                    event_name=schedule.event_name,
                    aliases=schedule.aliases,
                    sessions=[
                        WeekendSessionDownload(
                            **session.model_dump(),
                            stages={name: DownloadStage() for name in STAGES},
                        ).model_dump(mode="json")
                        for session in schedule.sessions
                    ],
                )
                try:
                    with self.db.begin_nested():
                        self.db.add(weekend)
                        self.db.flush()
                except IntegrityError:
                    weekend = self.db.scalar(
                        select(WeekendDownload).where(
                            WeekendDownload.year == schedule.year,
                            WeekendDownload.round_number
                            == schedule.round_number,
                        )
                    )
                    if weekend is None:
                        raise
                    cached = True
        # Creation and job linking commit together. The row lock serializes
        # simultaneous selectors, including different aliases for one round.
        weekend = self._locked(weekend.id)
        job = self._job(weekend)
        if job is None:
            job = self._new_job(weekend)
        elif job.status not in ACTIVE_JOBS and self._future_session_due(
            weekend
        ):
            self._reset_missing(weekend, future_only=True)
            job = self._new_job(weekend)
            cached = False
        self.db.commit()
        DurableJobService(self.db, publisher=self.publisher).dispatch(job)
        return self.response(weekend, cached=cached)

    def get(self, weekend_id: UUID) -> WeekendDownload:
        weekend = self.db.get(WeekendDownload, weekend_id)
        if weekend is None:
            raise LookupError("Race weekend download was not found.")
        return weekend

    def retry(self, weekend_id: UUID) -> WeekendDownloadResponse:
        weekend = self._locked(weekend_id)
        job = self._job(weekend)
        if job is not None and job.status in ACTIVE_JOBS:
            self.db.commit()
            return self.response(weekend, cached=True)
        self._reset_missing(weekend)
        if self._has_pending(weekend):
            job = self._new_job(weekend)
        self.db.commit()
        if job is not None:
            DurableJobService(self.db, publisher=self.publisher).dispatch(job)
        return self.response(weekend, cached=False)

    def response(
        self,
        weekend: WeekendDownload,
        *,
        cached: bool = True,
    ) -> WeekendDownloadResponse:
        job = self._job(weekend)
        job_status = job.status.value if job is not None else "QUEUED"
        states = [
            stage["status"]
            for session in weekend.sessions
            for stage in session["stages"].values()
        ]
        status = job_status
        if job_status == "COMPLETED":
            status = (
                "READY" if all(s == "COMPLETED" for s in states) else "PARTIAL"
            )
        return WeekendDownloadResponse(
            id=weekend.id,
            year=weekend.year,
            round_number=weekend.round_number,
            event_name=weekend.event_name,
            meeting_id=weekend.meeting_id,
            status=status,
            progress_percentage=job.progress_percentage if job else 0,
            cached=cached,
            sessions=weekend.sessions,
            job=DurableJobResponse.model_validate(job) if job else None,
        )

    def run(self, weekend_id: UUID) -> dict[str, object]:
        weekend = self.get(weekend_id)
        sessions = deepcopy(weekend.sessions)
        failed = False
        for index, session in enumerate(sessions):
            self._cancellation_check()
            stages = session["stages"]
            if all(s["status"] in FINISHED_STAGES for s in stages.values()):
                continue
            scheduled_at = session.get("scheduled_at")
            if scheduled_at and datetime.fromisoformat(
                scheduled_at
            ) > datetime.now(UTC):
                for stage in stages.values():
                    stage.update(
                        status="UNAVAILABLE",
                        details={
                            "reason": "SESSION_NOT_STARTED",
                        },
                    )
                self._checkpoint(weekend, sessions)
                continue
            try:
                loaded = self.provider.load_full_session(
                    year=weekend.year,
                    round_number=weekend.round_number,
                    session_identifier=session["identifier"],
                )
                self._cancellation_check()
            except (JobCancellationRequested, JobStateError):
                raise
            except Exception:
                for stage in stages.values():
                    if stage["status"] not in FINISHED_STAGES:
                        stage.update(
                            status="FAILED",
                            details={
                                "message": "Provider session could not be loaded.",
                            },
                        )
                self._checkpoint(weekend, sessions)
                failed = True
                continue
            for stage_index, name in enumerate(STAGES):
                if stages[name]["status"] in FINISHED_STAGES:
                    continue
                self._cancellation_check()
                stages[name] = {"status": "RUNNING", "details": {}}
                self._checkpoint(weekend, sessions)
                try:
                    status, details = self._run_stage(
                        name,
                        weekend,
                        session,
                        loaded,
                        checkpoint=lambda: self._checkpoint(weekend, sessions),
                    )
                except (JobCancellationRequested, JobStateError):
                    self.db.rollback()
                    raise
                except Exception as error:
                    self.db.rollback()
                    logger.warning(
                        "Weekend import stage failed stage=%s error_type=%s.",
                        name,
                        type(error).__name__,
                    )
                    status = "FAILED"
                    details = {"message": f"The {name} import needs a retry."}
                    failed = True
                self._cancellation_check()
                stages[name] = {"status": status, "details": details}
                self._checkpoint(weekend, sessions)
                self._progress_callback(
                    int(
                        (index * len(STAGES) + stage_index + 1)
                        * 99
                        / (len(sessions) * len(STAGES))
                    )
                )
            del loaded
        if failed:
            raise WeekendDownloadError("Some weekend data needs a retry.")
        states = [
            stage["status"]
            for session in sessions
            for stage in session["stages"].values()
        ]
        return {
            "weekend_id": str(weekend.id),
            "meeting_id": str(weekend.meeting_id)
            if weekend.meeting_id
            else None,
            "coverage": "COMPLETE"
            if all(s == "COMPLETED" for s in states)
            else "PARTIAL",
            "sessions_processed": len(sessions),
        }

    def _run_stage(self, name, weekend, session, loaded, *, checkpoint):
        check = self._cancellation_check
        if name == "results":
            if session.get("import_job_id") is None:
                target = ImportJob(
                    source=DataSource.FASTF1,
                    year=weekend.year,
                    event_name=weekend.event_name,
                    session_type=session["name"],
                )
                self.db.add(target)
                self.db.flush()
                session["import_job_id"] = str(target.id)
                checkpoint()
            target = SessionImportService(
                self.db,
                cancellation_check=check,
            ).run(
                UUID(session["import_job_id"]),
                loaded_session=loaded,
                resume=True,
            )
            race_session = self.db.get(RaceSession, target.imported_session_id)
            session["race_session_id"] = str(race_session.id)
            weekend.meeting_id = race_session.meeting_id
            return "COMPLETED", {"race_session_id": str(race_session.id)}

        if session.get("race_session_id") is None:
            raise WeekendDownloadError(
                "Session results must be imported first."
            )
        session_id = UUID(session["race_session_id"])
        if name == "laps":
            written, skipped = LapImportService(
                self.db,
                cancellation_check=check,
            ).import_laps(session_id, loaded_session=loaded)
            return ("COMPLETED" if written and not skipped else "PARTIAL"), {
                "laps_written": written,
                "laps_skipped": skipped,
            }
        if name == "telemetry":
            service = SessionTelemetryImportService(
                self.db, cancellation_check=check
            )
            if session.get("telemetry_import_id") is None:
                target = service.create(
                    race_session_id=session_id,
                    payload=SessionTelemetryImportCreate(
                        max_laps_per_driver=None,
                        clean_laps_only=False,
                    ),
                )
                session["telemetry_import_id"] = str(target.id)
                checkpoint()
            target = service.run(
                UUID(session["telemetry_import_id"]),
                loaded_session=loaded,
                resume=True,
            )
            if target.laps_failed:
                raise WeekendDownloadError("Some lap telemetry needs a retry.")
            return (
                "COMPLETED"
                if target.status.value == "COMPLETED"
                else "PARTIAL"
            ), {
                "laps_requested": target.laps_requested,
                "laps_with_telemetry": target.laps_with_telemetry,
                "laps_unavailable": target.laps_unavailable,
                "points_written": target.points_written,
            }
        if name == "map":
            service = SessionMapImportService(
                self.db, cancellation_check=check
            )
            if session.get("map_import_id") is None:
                target = service.create(
                    race_session_id=session_id,
                    payload=SessionMapImportCreate(sample_interval_ms=0),
                )
                session["map_import_id"] = str(target.id)
                checkpoint()
            target = service.run(
                UUID(session["map_import_id"]),
                loaded_session=loaded,
                resume=True,
            )
            if target.drivers_failed:
                raise WeekendDownloadError(
                    "Some driver positions need a retry."
                )
            return (
                "COMPLETED"
                if target.status.value == "COMPLETED"
                else "PARTIAL"
            ), {
                "map_import_id": str(target.id),
                "drivers_total": target.drivers_total,
                "sample_interval_ms": 0,
            }
        if name == "context":
            result = RaceContextService(self.db).import_context(
                session_id,
                loaded_session=loaded,
            )
            status = (
                "COMPLETED" if result.weather_samples_imported else "PARTIAL"
            )
            return status, result.model_dump(mode="json")
        if name == "metadata":
            return self._store_metadata(session_id, loaded)
        raise ValueError("Unknown weekend stage.")

    def _store_metadata(self, session_id, loaded):
        metadata = {}
        missing = []
        for field in ("session_info", "session_status", "track_status"):
            try:
                value = getattr(loaded, field)
                metadata[field] = self._json_value(value)
            except Exception:
                missing.append(field)
        try:
            circuit = loaded.get_circuit_info()
            if circuit is None:
                missing.append("circuit")
            else:
                metadata["circuit"] = {
                    field: self._json_value(getattr(circuit, field))
                    for field in (
                        "corners",
                        "marshal_lights",
                        "marshal_sectors",
                        "rotation",
                    )
                }
        except Exception:
            missing.append("circuit")
        metadata["unavailable"] = missing
        self._cancellation_check()
        self.db.get(RaceSession, session_id).source_metadata = metadata
        self.db.commit()
        return ("PARTIAL" if missing else "COMPLETED"), {
            "unavailable": missing
        }

    @staticmethod
    def _json_value(value):
        if isinstance(value, (pd.DataFrame, pd.Series)):
            return json.loads(
                value.to_json(orient="records", date_format="iso")
            )
        return json.loads(json.dumps(value, default=str, allow_nan=False))

    def _checkpoint(self, weekend, sessions):
        weekend.sessions = deepcopy(sessions)
        self.db.commit()

    def _job(self, weekend):
        return (
            self.db.get(DurableJob, weekend.durable_job_id)
            if weekend.durable_job_id
            else None
        )

    def _locked(self, weekend_id):
        weekend = self.db.scalar(
            select(WeekendDownload)
            .where(
                WeekendDownload.id == weekend_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if weekend is None:
            raise LookupError("Race weekend download was not found.")
        return weekend

    def _new_job(self, weekend):
        # A new attempt has its own key; ordinary selection reuses the linked job.
        from uuid import uuid4

        job = DurableJob(
            job_type=DurableJobType.WEEKEND_IMPORT,
            target_id=weekend.id,
            idempotency_key=f"weekend:{weekend.id}:{uuid4()}",
            status=DurableJobStatus.QUEUED,
            max_attempts=settings.job_max_attempts,
            payload={"weekend_id": str(weekend.id)},
        )
        self.db.add(job)
        self.db.flush()
        weekend.durable_job_id = job.id
        return job

    @staticmethod
    def _future_session_due(weekend):
        now = datetime.now(UTC)
        return any(
            stage.get("details", {}).get("reason") == "SESSION_NOT_STARTED"
            and session.get("scheduled_at")
            and datetime.fromisoformat(session["scheduled_at"]) <= now
            for session in weekend.sessions
            for stage in session["stages"].values()
        )

    @staticmethod
    def _reset_missing(weekend, *, future_only=False):
        sessions = deepcopy(weekend.sessions)
        for session in sessions:
            for name, stage in session["stages"].items():
                if stage["status"] == "COMPLETED":
                    continue
                if (
                    future_only
                    and stage.get("details", {}).get("reason")
                    != "SESSION_NOT_STARTED"
                ):
                    continue
                stage.update(status="PENDING", details={})
                # Fresh detail records reattempt unavailable laps/positions.
                if name == "telemetry":
                    session["telemetry_import_id"] = None
                if name == "map":
                    session["map_import_id"] = None
        weekend.sessions = sessions

    @staticmethod
    def _has_pending(weekend):
        return any(
            stage["status"] == "PENDING"
            for session in weekend.sessions
            for stage in session["stages"].values()
        )
