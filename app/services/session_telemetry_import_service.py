from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.driver import Driver
from app.models.lap import Lap
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.session_telemetry_import import (
    SessionTelemetryDriverStatus,
    SessionTelemetryImport,
    SessionTelemetryImportStatus,
)
from app.models.telemetry_point import TelemetryPoint
from app.schemas.session_telemetry_import import (
    SessionTelemetryCoverageDriverResponse,
    SessionTelemetryCoverageResponse,
    SessionTelemetryImportCreate,
    SessionTelemetryImportDriverResponse,
)
from app.services.telemetry_service import (
    TelemetryImportError,
    TelemetryImportService,
    TelemetryUnavailableError,
)
from app.services.durable_job_service import (
    JobCancellationRequested,
    JobStateError,
)
from app.services.error_message import safe_provider_error_message


class SessionTelemetryImportNotFoundError(LookupError):
    pass


class SessionTelemetrySourceSessionNotFoundError(LookupError):
    pass


class SessionTelemetryImportStateError(RuntimeError):
    pass


class SessionTelemetryImportError(RuntimeError):
    pass


class SessionTelemetryImportService:
    def __init__(
        self,
        db: Session,
        lap_telemetry_importer: TelemetryImportService | None = None,
        *,
        cancellation_check: Callable[[], None] | None = None,
        progress_callback: Callable[[int], None] | None = None,
    ) -> None:
        self.db = db
        self.lap_telemetry_importer = (
            lap_telemetry_importer
            if lap_telemetry_importer is not None
            else TelemetryImportService(db)
        )
        self._cancellation_check = cancellation_check
        self._progress_callback = progress_callback

    def create(
        self,
        *,
        race_session_id: UUID,
        payload: SessionTelemetryImportCreate,
        idempotency_key: str | None = None,
    ) -> SessionTelemetryImport:
        normalized_key = (
            idempotency_key.strip()[:255]
            if idempotency_key and idempotency_key.strip()
            else None
        )
        if normalized_key is not None:
            existing = self.db.scalar(
                select(SessionTelemetryImport).where(
                    SessionTelemetryImport.idempotency_key == normalized_key
                )
            )
            if existing is not None:
                return existing

        race_session = self.db.get(RaceSession, race_session_id)

        if race_session is None:
            raise SessionTelemetrySourceSessionNotFoundError(
                "Race session not found."
            )

        drivers = self._drivers_for_session(race_session_id)
        available_numbers = {driver.driver_number for driver in drivers}

        if payload.driver_numbers is not None:
            unknown_numbers = sorted(
                set(payload.driver_numbers) - available_numbers
            )

            if unknown_numbers:
                raise SessionTelemetryImportStateError(
                    "These drivers are not in the selected session: "
                    + ", ".join(unknown_numbers)
                )

            drivers = [
                driver
                for driver in drivers
                if driver.driver_number in payload.driver_numbers
            ]

        if not drivers:
            raise SessionTelemetryImportStateError(
                "No drivers with imported session results are available."
            )

        driver_results = [
            self._new_driver_result(
                driver=driver,
                selected_laps=self._select_laps_for_driver(
                    race_session_id=race_session_id,
                    driver=driver,
                    max_laps=payload.max_laps_per_driver,
                    clean_laps_only=payload.clean_laps_only,
                ),
            )
            for driver in drivers
        ]

        telemetry_import = SessionTelemetryImport(
            race_session_id=race_session_id,
            source="FASTF1",
            status=SessionTelemetryImportStatus.PENDING,
            requested_driver_numbers=payload.driver_numbers,
            max_laps_per_driver=payload.max_laps_per_driver,
            clean_laps_only=payload.clean_laps_only,
            driver_results=driver_results,
            drivers_total=len(driver_results),
            laps_requested=sum(
                int(result["laps_requested"]) for result in driver_results
            ),
            idempotency_key=normalized_key,
        )

        self.db.add(telemetry_import)
        self.db.commit()
        self.db.refresh(telemetry_import)

        return telemetry_import

    def get(
        self,
        telemetry_import_id: UUID,
    ) -> SessionTelemetryImport:
        telemetry_import = self.db.get(
            SessionTelemetryImport,
            telemetry_import_id,
        )

        if telemetry_import is None:
            raise SessionTelemetryImportNotFoundError(
                "Session telemetry import was not found."
            )

        return telemetry_import

    def list_drivers(
        self,
        telemetry_import_id: UUID,
    ) -> list[SessionTelemetryImportDriverResponse]:
        telemetry_import = self.get(telemetry_import_id)

        return [
            SessionTelemetryImportDriverResponse.model_validate(result)
            for result in telemetry_import.driver_results
        ]

    def get_coverage(
        self,
        race_session_id: UUID,
    ) -> SessionTelemetryCoverageResponse:
        race_session = self.db.get(RaceSession, race_session_id)

        if race_session is None:
            raise SessionTelemetrySourceSessionNotFoundError(
                "Race session not found."
            )

        rows = self.db.execute(
            select(
                Driver.driver_number,
                Driver.abbreviation,
                Driver.full_name,
                func.count(func.distinct(Lap.id)),
                func.count(func.distinct(TelemetryPoint.lap_id)),
                func.count(TelemetryPoint.id),
            )
            .join(
                SessionResult,
                SessionResult.driver_id == Driver.id,
            )
            .outerjoin(
                Lap,
                and_(
                    Lap.driver_id == Driver.id,
                    Lap.race_session_id == race_session_id,
                ),
            )
            .outerjoin(
                TelemetryPoint,
                TelemetryPoint.lap_id == Lap.id,
            )
            .where(SessionResult.race_session_id == race_session_id)
            .group_by(
                Driver.id,
                Driver.driver_number,
                Driver.abbreviation,
                Driver.full_name,
            )
        ).all()

        drivers = [
            SessionTelemetryCoverageDriverResponse(
                driver_number=driver_number,
                abbreviation=abbreviation,
                full_name=full_name,
                imported_lap_count=int(imported_lap_count),
                telemetry_lap_count=int(telemetry_lap_count),
                telemetry_point_count=int(telemetry_point_count),
                coverage_percent=self._coverage_percent(
                    int(telemetry_lap_count),
                    int(imported_lap_count),
                ),
            )
            for (
                driver_number,
                abbreviation,
                full_name,
                imported_lap_count,
                telemetry_lap_count,
                telemetry_point_count,
            ) in rows
        ]

        drivers.sort(
            key=lambda driver: self._driver_sort_key(driver.driver_number)
        )

        latest_import = self.db.scalar(
            select(SessionTelemetryImport)
            .where(SessionTelemetryImport.race_session_id == race_session_id)
            .order_by(SessionTelemetryImport.created_at.desc())
            .limit(1)
        )

        imported_lap_count = sum(
            driver.imported_lap_count for driver in drivers
        )
        telemetry_lap_count = sum(
            driver.telemetry_lap_count for driver in drivers
        )
        telemetry_point_count = sum(
            driver.telemetry_point_count for driver in drivers
        )

        return SessionTelemetryCoverageResponse(
            race_session_id=race_session_id,
            latest_telemetry_import_id=(
                latest_import.id if latest_import is not None else None
            ),
            latest_telemetry_import_status=(
                latest_import.status if latest_import is not None else None
            ),
            eligible_driver_count=len(drivers),
            drivers_with_telemetry=sum(
                driver.telemetry_lap_count > 0 for driver in drivers
            ),
            imported_lap_count=imported_lap_count,
            telemetry_lap_count=telemetry_lap_count,
            telemetry_point_count=telemetry_point_count,
            coverage_percent=self._coverage_percent(
                telemetry_lap_count,
                imported_lap_count,
            ),
            any_telemetry_available=telemetry_point_count > 0,
            drivers=drivers,
        )

    def run(
        self,
        telemetry_import_id: UUID,
        *,
        loaded_session=None,
        resume: bool = False,
    ) -> SessionTelemetryImport:
        telemetry_import = self.get(telemetry_import_id)

        if (
            telemetry_import.status == SessionTelemetryImportStatus.RUNNING
            and not resume
        ):
            raise SessionTelemetryImportStateError(
                "This session telemetry import is already running."
            )

        if telemetry_import.status == SessionTelemetryImportStatus.COMPLETED:
            return telemetry_import

        telemetry_import.status = SessionTelemetryImportStatus.RUNNING
        telemetry_import.error_message = None
        telemetry_import.completed_at = None

        if telemetry_import.started_at is None:
            telemetry_import.started_at = datetime.now(UTC)

        self.db.commit()
        self._report_progress(5)

        try:
            self._check_cancellation()
            race_session, meeting = self._load_source_context(telemetry_import)
            fastf1_session = loaded_session
            if fastf1_session is None:
                fastf1_session = self.lap_telemetry_importer.fastf1_provider.load_telemetry_session(
                    year=meeting.year,
                    event_name=meeting.name,
                    session_identifier=race_session.session_identifier,
                )
            self._check_cancellation()
        except JobCancellationRequested:
            self.db.rollback()
            raise
        except JobStateError:
            self.db.rollback()
            raise
        except Exception as error:
            self._mark_job_failed(telemetry_import.id, error)

            raise SessionTelemetryImportError(
                "FastF1 could not load telemetry for this session."
            ) from error

        drivers_by_number = {
            driver.driver_number: driver
            for driver in self._drivers_for_session(race_session.id)
        }

        for result in self._driver_results(telemetry_import.id):
            self._check_cancellation()
            status = result["status"]

            if status in {
                SessionTelemetryDriverStatus.READY.value,
                SessionTelemetryDriverStatus.SKIPPED.value,
            } or (
                status == SessionTelemetryDriverStatus.PARTIAL.value
                and not result.get("failed_lap_numbers")
            ):
                continue

            driver_number = str(result["driver_number"])
            driver = drivers_by_number.get(driver_number)

            if driver is None:
                self._save_driver_result(
                    telemetry_import_id=telemetry_import.id,
                    driver_number=driver_number,
                    result=self._failed_driver_result(
                        result,
                        "Driver is no longer in the source session.",
                    ),
                )
            else:
                self._import_driver(
                    telemetry_import_id=telemetry_import.id,
                    race_session=race_session,
                    driver=driver,
                    result=result,
                    fastf1_session=fastf1_session,
                )

            self._refresh_job_summary(
                telemetry_import.id,
                final=False,
            )
            self._report_progress(
                self.get(telemetry_import.id).progress_percentage
            )

        self._refresh_job_summary(
            telemetry_import.id,
            final=True,
        )
        self._report_progress(100)

        return self.get(telemetry_import.id)

    def _load_source_context(
        self,
        telemetry_import: SessionTelemetryImport,
    ) -> tuple[RaceSession, Meeting]:
        context = self.db.execute(
            select(RaceSession, Meeting)
            .join(
                Meeting,
                RaceSession.meeting_id == Meeting.id,
            )
            .where(RaceSession.id == telemetry_import.race_session_id)
        ).one_or_none()

        if context is None:
            raise SessionTelemetrySourceSessionNotFoundError(
                "The source race session no longer exists."
            )

        return context

    def _import_driver(
        self,
        *,
        telemetry_import_id: UUID,
        race_session: RaceSession,
        driver: Driver,
        result: dict[str, object],
        fastf1_session,
    ) -> None:
        self._check_cancellation()
        running_result = {
            **result,
            "status": SessionTelemetryDriverStatus.RUNNING.value,
            "unavailable_lap_numbers": [],
            "failed_lap_numbers": [],
            "laps_processed": 0,
            "laps_with_telemetry": 0,
            "laps_unavailable": 0,
            "laps_failed": 0,
            "points_written": 0,
            "coverage_percent": None,
            "error_message": None,
            "started_at": self._iso_now(),
            "completed_at": None,
        }

        self._save_driver_result(
            telemetry_import_id=telemetry_import_id,
            driver_number=driver.driver_number,
            result=running_result,
            current_driver_number=driver.driver_number,
        )

        selected_lap_numbers = [
            int(lap_number)
            for lap_number in running_result["selected_lap_numbers"]
        ]

        if not selected_lap_numbers:
            self._save_driver_result(
                telemetry_import_id=telemetry_import_id,
                driver_number=driver.driver_number,
                result=self._skipped_driver_result(
                    running_result,
                    "No eligible stored laps matched this import.",
                ),
            )
            return

        stored_laps = self._selected_stored_laps(
            race_session_id=race_session.id,
            driver_id=driver.id,
            selected_lap_numbers=selected_lap_numbers,
        )
        laps_by_number = {lap.lap_number: lap for lap in stored_laps}

        unavailable_laps: list[int] = []
        failed_laps: list[int] = []
        successful_laps = 0
        points_written = 0

        for lap_number in selected_lap_numbers:
            self._check_cancellation()
            lap = laps_by_number.get(lap_number)

            if lap is None:
                failed_laps.append(lap_number)
                continue

            try:
                lap_points = (
                    self.lap_telemetry_importer.import_loaded_lap_telemetry(
                        lap=lap,
                        driver=driver,
                        race_session=race_session,
                        fastf1_session=fastf1_session,
                    )
                )

                if lap_points == 0:
                    unavailable_laps.append(lap_number)
                    continue

                successful_laps += 1
                points_written += lap_points

            except JobCancellationRequested:
                self.db.rollback()
                raise
            except JobStateError:
                self.db.rollback()
                raise
            except TelemetryUnavailableError:
                unavailable_laps.append(lap_number)

            except TelemetryImportError:
                failed_laps.append(lap_number)

            except Exception:
                failed_laps.append(lap_number)

        completed_result = {
            **running_result,
            "status": self._driver_status(
                successful_laps=successful_laps,
                unavailable_laps=unavailable_laps,
                failed_laps=failed_laps,
            ).value,
            "unavailable_lap_numbers": unavailable_laps,
            "failed_lap_numbers": failed_laps,
            "laps_processed": len(selected_lap_numbers),
            "laps_with_telemetry": successful_laps,
            "laps_unavailable": len(unavailable_laps),
            "laps_failed": len(failed_laps),
            "points_written": points_written,
            "coverage_percent": float(
                self._coverage_percent(
                    successful_laps,
                    len(selected_lap_numbers),
                )
            ),
            "error_message": self._driver_message(
                unavailable_laps,
                failed_laps,
            ),
            "completed_at": self._iso_now(),
        }

        self._save_driver_result(
            telemetry_import_id=telemetry_import_id,
            driver_number=driver.driver_number,
            result=completed_result,
        )

    def _check_cancellation(self) -> None:
        if self._cancellation_check is not None:
            self._cancellation_check()

    def _report_progress(self, progress_percentage: int) -> None:
        if self._progress_callback is not None:
            self._progress_callback(progress_percentage)

    def _selected_stored_laps(
        self,
        *,
        race_session_id: UUID,
        driver_id: UUID,
        selected_lap_numbers: list[int],
    ) -> list[Lap]:
        laps = self.db.scalars(
            select(Lap)
            .where(
                Lap.race_session_id == race_session_id,
                Lap.driver_id == driver_id,
                Lap.lap_number.in_(selected_lap_numbers),
            )
            .order_by(Lap.lap_number)
        ).all()

        laps_by_number = {lap.lap_number: lap for lap in laps}

        return [
            laps_by_number[lap_number]
            for lap_number in selected_lap_numbers
            if lap_number in laps_by_number
        ]

    def _select_laps_for_driver(
        self,
        *,
        race_session_id: UUID,
        driver: Driver,
        max_laps: int | None,
        clean_laps_only: bool,
    ) -> list[Lap]:
        laps = self.db.scalars(
            select(Lap)
            .where(
                Lap.race_session_id == race_session_id,
                Lap.driver_id == driver.id,
            )
            .order_by(Lap.lap_number)
        ).all()

        if max_laps is None and not clean_laps_only:
            # Full-weekend imports attempt every stored lap, including pit,
            # deleted and incomplete laps. Missing telemetry is reported.
            return list(laps)

        eligible_laps = [
            lap
            for lap in laps
            if (
                self._is_clean_green_lap(lap)
                if clean_laps_only
                else self._is_usable_lap(lap)
            )
        ]

        stint_groups = self._split_stints(eligible_laps)
        selected: list[Lap] = []
        index = 0

        lap_limit = max_laps if max_laps is not None else len(eligible_laps)
        while len(selected) < lap_limit:
            added_lap = False

            for stint in stint_groups:
                if index >= len(stint):
                    continue

                selected.append(stint[index])
                added_lap = True

                if len(selected) == lap_limit:
                    break

            if not added_lap:
                break

            index += 1

        return sorted(
            selected,
            key=lambda lap: lap.lap_number,
        )

    def _drivers_for_session(
        self,
        race_session_id: UUID,
    ) -> list[Driver]:
        drivers = list(
            self.db.scalars(
                select(Driver)
                .join(
                    SessionResult,
                    SessionResult.driver_id == Driver.id,
                )
                .where(SessionResult.race_session_id == race_session_id)
            ).all()
        )

        return sorted(
            drivers,
            key=lambda driver: self._driver_sort_key(driver.driver_number),
        )

    def _new_driver_result(
        self,
        *,
        driver: Driver,
        selected_laps: list[Lap],
    ) -> dict[str, object]:
        selected_lap_numbers = [lap.lap_number for lap in selected_laps]

        return {
            "driver_number": driver.driver_number,
            "abbreviation": driver.abbreviation,
            "full_name": driver.full_name,
            "status": SessionTelemetryDriverStatus.PENDING.value,
            "selected_lap_numbers": selected_lap_numbers,
            "unavailable_lap_numbers": [],
            "failed_lap_numbers": [],
            "laps_requested": len(selected_lap_numbers),
            "laps_processed": 0,
            "laps_with_telemetry": 0,
            "laps_unavailable": 0,
            "laps_failed": 0,
            "points_written": 0,
            "coverage_percent": None,
            "error_message": None,
            "started_at": None,
            "completed_at": None,
        }

    def _save_driver_result(
        self,
        *,
        telemetry_import_id: UUID,
        driver_number: str,
        result: dict[str, object],
        current_driver_number: str | None = None,
    ) -> None:
        telemetry_import = self.get(telemetry_import_id)
        results = [dict(item) for item in telemetry_import.driver_results]

        for index, item in enumerate(results):
            if item["driver_number"] == driver_number:
                results[index] = result
                break
        else:
            raise RuntimeError("Telemetry import driver result disappeared.")

        telemetry_import.driver_results = results

        if current_driver_number is not None:
            telemetry_import.current_driver_number = current_driver_number

        self.db.commit()

    def _driver_results(
        self,
        telemetry_import_id: UUID,
    ) -> list[dict[str, object]]:
        telemetry_import = self.get(telemetry_import_id)

        return [dict(item) for item in telemetry_import.driver_results]

    def _refresh_job_summary(
        self,
        telemetry_import_id: UUID,
        *,
        final: bool,
    ) -> None:
        telemetry_import = self.get(telemetry_import_id)
        results = self._driver_results(telemetry_import_id)

        ready = sum(
            result["status"] == SessionTelemetryDriverStatus.READY.value
            for result in results
        )
        partial = sum(
            result["status"] == SessionTelemetryDriverStatus.PARTIAL.value
            for result in results
        )
        skipped = sum(
            result["status"] == SessionTelemetryDriverStatus.SKIPPED.value
            for result in results
        )
        failed = sum(
            result["status"] == SessionTelemetryDriverStatus.FAILED.value
            for result in results
        )

        processed = ready + partial + skipped + failed

        telemetry_import.drivers_total = len(results)
        telemetry_import.drivers_processed = processed
        telemetry_import.drivers_completed = ready
        telemetry_import.drivers_partial = partial
        telemetry_import.drivers_skipped = skipped
        telemetry_import.drivers_failed = failed

        telemetry_import.laps_requested = sum(
            int(result["laps_requested"]) for result in results
        )
        telemetry_import.laps_processed = sum(
            int(result["laps_processed"]) for result in results
        )
        telemetry_import.laps_with_telemetry = sum(
            int(result["laps_with_telemetry"]) for result in results
        )
        telemetry_import.laps_unavailable = sum(
            int(result["laps_unavailable"]) for result in results
        )
        telemetry_import.laps_failed = sum(
            int(result["laps_failed"]) for result in results
        )
        telemetry_import.points_written = sum(
            int(result["points_written"]) for result in results
        )

        if final:
            telemetry_import.progress_percentage = 100
            telemetry_import.current_driver_number = None
            telemetry_import.completed_at = datetime.now(UTC)

            if ready == len(results):
                telemetry_import.status = (
                    SessionTelemetryImportStatus.COMPLETED
                )
                telemetry_import.error_message = None
            elif ready + partial > 0:
                telemetry_import.status = SessionTelemetryImportStatus.PARTIAL
                telemetry_import.error_message = (
                    "Some selected driver laps did not expose "
                    "usable FastF1 telemetry."
                )
            else:
                telemetry_import.status = SessionTelemetryImportStatus.FAILED
                telemetry_import.error_message = (
                    "No selected driver lap produced stored telemetry."
                )
        elif telemetry_import.drivers_total:
            telemetry_import.progress_percentage = int(
                round(processed / telemetry_import.drivers_total * 100)
            )

        self.db.commit()

    def _mark_job_failed(
        self,
        telemetry_import_id: UUID,
        error: Exception,
    ) -> None:
        self.db.rollback()
        telemetry_import = self.get(telemetry_import_id)

        telemetry_import.status = SessionTelemetryImportStatus.FAILED
        telemetry_import.error_message = self._error_text(error)
        telemetry_import.current_driver_number = None
        telemetry_import.completed_at = datetime.now(UTC)

        self.db.commit()

    @staticmethod
    def _is_clean_green_lap(lap: Lap) -> bool:
        return (
            lap.lap_time_ms is not None
            and lap.track_status == "1"
            and lap.is_accurate is True
            and lap.deleted is not True
            and lap.deleted_reason is None
            and lap.fastf1_generated is not True
            and lap.pit_in_time_ms is None
            and lap.pit_out_time_ms is None
        )

    @staticmethod
    def _is_usable_lap(lap: Lap) -> bool:
        return (
            lap.lap_time_ms is not None
            and lap.deleted is not True
            and lap.fastf1_generated is not True
        )

    @staticmethod
    def _split_stints(laps: list[Lap]) -> list[list[Lap]]:
        if not laps:
            return []

        stints: list[list[Lap]] = []
        current_stint = [laps[0]]

        for lap in laps[1:]:
            previous_lap = current_stint[-1]
            source_stint = current_stint[0].stint

            starts_new_stint = (
                lap.stint is not None and lap.stint != source_stint
            ) or (
                previous_lap.compound is not None
                and lap.compound is not None
                and previous_lap.compound != lap.compound
            )

            if starts_new_stint:
                stints.append(current_stint)
                current_stint = [lap]
            else:
                current_stint.append(lap)

        stints.append(current_stint)
        return stints

    @staticmethod
    def _driver_status(
        *,
        successful_laps: int,
        unavailable_laps: list[int],
        failed_laps: list[int],
    ) -> SessionTelemetryDriverStatus:
        if successful_laps > 0:
            if unavailable_laps or failed_laps:
                return SessionTelemetryDriverStatus.PARTIAL

            return SessionTelemetryDriverStatus.READY

        if failed_laps:
            return SessionTelemetryDriverStatus.FAILED

        return SessionTelemetryDriverStatus.SKIPPED

    def _skipped_driver_result(
        self,
        result: dict[str, object],
        message: str,
    ) -> dict[str, object]:
        return {
            **result,
            "status": SessionTelemetryDriverStatus.SKIPPED.value,
            "coverage_percent": 0.0,
            "error_message": message,
            "completed_at": self._iso_now(),
        }

    def _failed_driver_result(
        self,
        result: dict[str, object],
        message: str,
    ) -> dict[str, object]:
        return {
            **result,
            "status": SessionTelemetryDriverStatus.FAILED.value,
            "coverage_percent": 0.0,
            "error_message": message,
            "completed_at": self._iso_now(),
        }

    @staticmethod
    def _driver_message(
        unavailable_laps: list[int],
        failed_laps: list[int],
    ) -> str | None:
        messages: list[str] = []

        if unavailable_laps:
            messages.append(
                "FastF1 did not expose telemetry for laps: "
                + ", ".join(str(lap) for lap in unavailable_laps)
            )

        if failed_laps:
            messages.append(
                "Telemetry import failed for laps: "
                + ", ".join(str(lap) for lap in failed_laps)
            )

        return " ".join(messages) or None

    @staticmethod
    def _coverage_percent(
        numerator: int,
        denominator: int,
    ) -> Decimal:
        if denominator == 0:
            return Decimal("0.00")

        return (
            Decimal(numerator) * Decimal("100") / Decimal(denominator)
        ).quantize(Decimal("0.01"))

    @staticmethod
    def _driver_sort_key(driver_number: str) -> tuple[int, int | str]:
        if driver_number.isdigit():
            return 0, int(driver_number)

        return 1, driver_number

    @staticmethod
    def _iso_now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _error_text(error: Exception) -> str:
        return safe_provider_error_message(
            error,
            operation="FastF1 telemetry import",
        )
