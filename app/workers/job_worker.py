from __future__ import annotations

import logging
from contextlib import contextmanager
import socket
from threading import Event, Thread
import time
from datetime import UTC, datetime
from typing import Callable
from uuid import UUID, uuid4

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.redis import create_sync_redis_client
from app.db.database import SessionLocal
from app.models.durable_job import DurableJob, DurableJobType
from app.services.durable_job_service import (
    DurableJobService,
    JobCancellationRequested,
    JobStateError,
)
from app.services.durable_job_target_service import (
    mark_cancelled_import_target,
    synchronize_cancelled_import_targets,
)
from app.services.fantasy_service import FantasyService
from app.services.lap_service import LapImportService
from app.services.race_context_service import RaceContextService
from app.services.session_import_service import SessionImportService
from app.services.session_map_import_service import SessionMapImportService
from app.services.session_telemetry_import_service import (
    SessionTelemetryImportService,
)
from app.services.weekend_download_service import WeekendDownloadService
from app.workers.job_queue import RedisStreamJobPublisher


logger = logging.getLogger(__name__)


class DurableJobWorker:
    """Redis Streams consumer that always claims PostgreSQL job state first."""

    def __init__(
        self,
        redis: Redis,
        *,
        worker_id: str | None = None,
    ) -> None:
        self.redis = redis
        self.publisher = RedisStreamJobPublisher(redis)
        self.worker_id = worker_id or (f"{socket.gethostname()}-{uuid4()}")
        self._last_recovery_at: datetime | None = None

    def run_forever(self) -> None:
        self.publisher.ensure_consumer_group()
        logger.info("Durable job worker started id=%s.", self.worker_id)
        while True:
            try:
                self.run_once(block_ms=1_000)
            except RedisError:
                logger.warning("Durable job worker lost Redis connectivity.")
                time.sleep(1)

    def run_once(self, *, block_ms: int = 0) -> int:
        self._recover_if_due()
        reclaimed_entries = self._claim_stale_stream_entries()
        messages = []
        if reclaimed_entries:
            messages.append((settings.job_stream_key, reclaimed_entries))
        messages.extend(
            self.redis.xreadgroup(
                settings.job_consumer_group,
                self.worker_id,
                {settings.job_stream_key: ">"},
                count=1,
                block=block_ms or None,
            )
        )
        processed = 0
        for _, entries in messages:
            for message_id, values in entries:
                self._process_message(values)
                self.redis.xack(
                    settings.job_stream_key,
                    settings.job_consumer_group,
                    message_id,
                )
                processed += 1
        return processed

    def _claim_stale_stream_entries(
        self,
    ) -> list[tuple[str, dict[str, str]]]:
        """Reclaim abandoned consumer-group entries after their DB lease.

        PostgreSQL still decides whether a reclaimed message can execute. This
        only prevents the Redis pending-entry list from growing after a worker
        crashes between delivery and acknowledgement.
        """
        _, entries, _ = self.redis.xautoclaim(
            settings.job_stream_key,
            settings.job_consumer_group,
            self.worker_id,
            min_idle_time=settings.job_lease_seconds * 1_000,
            start_id="0-0",
            count=10,
        )
        return entries

    def _recover_if_due(self) -> None:
        now = datetime.now(UTC)
        if (
            self._last_recovery_at is not None
            and (now - self._last_recovery_at).total_seconds()
            < settings.job_recovery_interval_seconds
        ):
            return

        db = SessionLocal()
        try:
            service = DurableJobService(db, publisher=self.publisher)
            service.recover_expired_leases()
            synchronize_cancelled_import_targets(db)
            service.dispatch_ready(
                minimum_age_seconds=settings.job_recovery_interval_seconds
            )
        finally:
            db.close()
        self._last_recovery_at = now

    def _process_message(self, values: dict[str, str]) -> None:
        raw_job_id = values.get("job_id")
        if raw_job_id is None:
            logger.warning("Ignored malformed durable job stream message.")
            return

        try:
            job_id = UUID(raw_job_id)
        except ValueError:
            logger.warning("Ignored durable job stream message with bad id.")
            return

        db = SessionLocal()
        try:
            service = DurableJobService(db, publisher=self.publisher)
            job = service.claim(job_id, lease_owner=self.worker_id)
            if job is None:
                return

            try:
                try:
                    with self._lease_heartbeat(job.id):
                        result = self._run_job(db, service, job)
                except JobCancellationRequested:
                    db.expire_all()
                    cancelled_job, _ = service.request_cancel(job.id)
                    mark_cancelled_import_target(db, cancelled_job)
                    service.complete(
                        job.id,
                        result={"cancelled": True},
                        lease_owner=self.worker_id,
                    )
                except Exception as error:
                    db.rollback()
                    service.fail(
                        job.id,
                        error,
                        lease_owner=self.worker_id,
                    )
                    logger.warning(
                        "Durable job failed type=%s error_type=%s.",
                        job.job_type.value,
                        type(error).__name__,
                    )
                else:
                    service.complete(
                        job.id,
                        result=result,
                        lease_owner=self.worker_id,
                    )
            except JobStateError:
                logger.info(
                    "Ignored stale durable-job worker completion type=%s.",
                    job.job_type.value,
                )
        finally:
            db.close()

    @contextmanager
    def _lease_heartbeat(self, job_id: UUID):
        """Long provider downloads must not outlive an otherwise healthy lease."""
        stopped = Event()

        def heartbeat() -> None:
            interval = max(0.1, settings.job_lease_seconds / 3)
            while not stopped.wait(interval):
                heartbeat_db = SessionLocal()
                try:
                    DurableJobService(heartbeat_db).renew_lease(
                        job_id,
                        lease_owner=self.worker_id,
                    )
                except JobStateError:
                    return
                except Exception:
                    logger.warning(
                        "Worker lease heartbeat failed job=%s.", job_id
                    )
                finally:
                    heartbeat_db.close()

        thread = Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stopped.set()
            thread.join(timeout=1)

    def _run_job(
        self,
        db,
        service: DurableJobService,
        job: DurableJob,
    ) -> dict[str, object]:
        service.raise_if_cancel_requested(job.id)
        target_id = job.target_id
        if target_id is None:
            raise ValueError("Durable job has no target identifier.")

        result = self._handlers(
            service,
            job.id,
            self.worker_id,
        )[job.job_type](db, target_id)
        service.raise_if_cancel_requested(job.id)
        return result

    @staticmethod
    def _handlers(
        service: DurableJobService,
        job_id: UUID,
        lease_owner: str,
    ) -> dict[DurableJobType, Callable[[object, UUID], dict[str, object]]]:
        def cancellation_check() -> None:
            check_db = SessionLocal()
            try:
                check_service = DurableJobService(check_db)
                check_service.raise_if_cancel_requested(job_id)
                check_service.renew_lease(
                    job_id,
                    lease_owner=lease_owner,
                )
            finally:
                check_db.close()

        def progress_callback(progress_percentage: int) -> None:
            progress_db = SessionLocal()
            try:
                DurableJobService(progress_db).update_progress(
                    job_id,
                    progress_percentage,
                    lease_owner=lease_owner,
                )
            finally:
                progress_db.close()

        def session_import(db, target_id: UUID) -> dict[str, object]:
            job = SessionImportService(
                db,
                cancellation_check=cancellation_check,
                progress_callback=progress_callback,
            ).run(target_id, resume=True)
            return {
                "import_job_id": str(job.id),
                "import_status": job.status.value,
                "imported_session_id": (
                    str(job.imported_session_id)
                    if job.imported_session_id is not None
                    else None
                ),
            }

        def laps_import(db, target_id: UUID) -> dict[str, object]:
            laps_upserted, laps_skipped = LapImportService(
                db,
                cancellation_check=cancellation_check,
                progress_callback=progress_callback,
            ).import_laps(target_id)
            return {
                "race_session_id": str(target_id),
                "laps_upserted": laps_upserted,
                "laps_skipped": laps_skipped,
            }

        def telemetry_import(db, target_id: UUID) -> dict[str, object]:
            imported = SessionTelemetryImportService(
                db,
                cancellation_check=cancellation_check,
                progress_callback=progress_callback,
            ).run(target_id, resume=True)
            return {
                "telemetry_import_id": str(imported.id),
                "import_status": imported.status.value,
                "progress_percentage": imported.progress_percentage,
            }

        def map_import(db, target_id: UUID) -> dict[str, object]:
            imported = SessionMapImportService(
                db,
                cancellation_check=cancellation_check,
                progress_callback=progress_callback,
            ).run(target_id, resume=True)
            return {
                "map_import_id": str(imported.id),
                "import_status": imported.status.value,
                "progress_percentage": imported.progress_percentage,
            }

        def race_context_import(db, target_id: UUID) -> dict[str, object]:
            progress_callback(5)
            imported = RaceContextService(db).import_context(target_id)
            progress_callback(95)
            return imported.model_dump(mode="json")

        def fantasy_score(db, target_id: UUID) -> dict[str, object]:
            progress_callback(5)
            score = FantasyService(db).score_available_questions(target_id)
            progress_callback(95)
            return score.model_dump(mode="json")

        def fantasy_finalize(db, target_id: UUID) -> dict[str, object]:
            progress_callback(5)
            finalized = FantasyService(db).finalize_weekend(target_id)
            progress_callback(95)
            return finalized.model_dump(mode="json")

        def weekend_import(db, target_id: UUID) -> dict[str, object]:
            return WeekendDownloadService(
                db,
                cancellation_check=cancellation_check,
                progress_callback=progress_callback,
            ).run(target_id)

        # The job id is deliberately closed over so bounded units of provider
        # work check cancellation and surface their progress independently of
        # the domain transaction.
        del service
        return {
            DurableJobType.WEEKEND_IMPORT: weekend_import,
            DurableJobType.SESSION_IMPORT: session_import,
            DurableJobType.SESSION_LAPS_IMPORT: laps_import,
            DurableJobType.SESSION_TELEMETRY_IMPORT: telemetry_import,
            DurableJobType.SESSION_MAP_IMPORT: map_import,
            DurableJobType.RACE_CONTEXT_IMPORT: race_context_import,
            DurableJobType.FANTASY_SCORE: fantasy_score,
            DurableJobType.FANTASY_FINALIZE: fantasy_finalize,
        }


def main() -> None:
    redis = create_sync_redis_client()
    try:
        DurableJobWorker(redis).run_forever()
    finally:
        redis.close()


if __name__ == "__main__":
    main()
