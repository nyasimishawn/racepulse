from bisect import bisect_right
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.driver import Driver
from app.models.lap import Lap
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_map_import import (
    SessionMapDriverStatus,
    SessionMapImport,
    SessionMapImportStatus,
)
from app.models.session_map_import_driver import SessionMapImportDriver
from app.models.session_map_sample import SessionMapSample
from app.models.session_result import SessionResult
from app.providers.fastf1_provider import FastF1Provider
from app.schemas.session_map_import import (
    SessionMapCoverageResponse,
    SessionMapImportCreate,
    SessionMapImportDriverResponse,
)


MAP_READY_MIN_SAMPLE_COUNT = 100
MAP_READY_MIN_COVERAGE_PERCENT = Decimal("70")
MAP_READY_MAX_GAP_MS = 2_000
INSERT_CHUNK_SIZE = 1_000


class SessionMapImportNotFoundError(LookupError):
    pass


class SessionMapSourceSessionNotFoundError(LookupError):
    pass


class SessionMapImportStateError(RuntimeError):
    pass


class SessionMapImportError(RuntimeError):
    pass


class SessionMapImportService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.fastf1_provider = FastF1Provider()

    def create(
        self,
        *,
        race_session_id: UUID,
        payload: SessionMapImportCreate,
    ) -> SessionMapImport:
        race_session = self.db.get(RaceSession, race_session_id)

        if race_session is None:
            raise SessionMapSourceSessionNotFoundError(
                "Race session not found."
            )

        rows = self.db.execute(
            select(SessionResult, Driver)
            .join(Driver, SessionResult.driver_id == Driver.id)
            .where(SessionResult.race_session_id == race_session_id)
        ).all()

        drivers = sorted(
            [driver for _, driver in rows],
            key=lambda driver: self._driver_sort_key(
                driver.driver_number
            ),
        )

        available_numbers = {
            driver.driver_number
            for driver in drivers
        }

        requested_numbers = payload.driver_numbers

        if requested_numbers is not None:
            unknown_numbers = sorted(
                set(requested_numbers) - available_numbers
            )

            if unknown_numbers:
                raise SessionMapImportStateError(
                    "These drivers are not in the selected session: "
                    + ", ".join(unknown_numbers)
                )

            drivers = [
                driver
                for driver in drivers
                if driver.driver_number in requested_numbers
            ]

        if not drivers:
            raise SessionMapImportStateError(
                "No drivers are available for this map import."
            )

        map_import = SessionMapImport(
            race_session_id=race_session_id,
            source="FASTF1",
            status=SessionMapImportStatus.PENDING,
            sample_interval_ms=payload.sample_interval_ms,
            requested_driver_numbers=requested_numbers,
            drivers_total=len(drivers),
        )

        self.db.add(map_import)
        self.db.flush()

        for driver in drivers:
            self.db.add(
                SessionMapImportDriver(
                    map_import_id=map_import.id,
                    driver_id=driver.id,
                    status=SessionMapDriverStatus.PENDING,
                )
            )

        self.db.commit()
        self.db.refresh(map_import)

        return map_import

    def get(self, map_import_id: UUID) -> SessionMapImport:
        map_import = self.db.get(SessionMapImport, map_import_id)

        if map_import is None:
            raise SessionMapImportNotFoundError(
                "Session map import was not found."
            )

        return map_import

    def list_drivers(
        self,
        map_import_id: UUID,
    ) -> list[SessionMapImportDriverResponse]:
        self.get(map_import_id)

        rows = self.db.execute(
            select(SessionMapImportDriver, Driver)
            .join(
                Driver,
                SessionMapImportDriver.driver_id == Driver.id,
            )
            .where(
                SessionMapImportDriver.map_import_id
                == map_import_id
            )
        ).all()

        rows.sort(
            key=lambda row: self._driver_sort_key(
                row[1].driver_number
            )
        )

        return [
            SessionMapImportDriverResponse(
                driver_number=driver.driver_number,
                abbreviation=driver.abbreviation,
                full_name=driver.full_name,
                status=item.status,
                raw_samples_seen=item.raw_samples_seen,
                valid_samples=item.valid_samples,
                samples_written=item.samples_written,
                first_sample_session_time_ms=(
                    item.first_sample_session_time_ms
                ),
                last_sample_session_time_ms=(
                    item.last_sample_session_time_ms
                ),
                largest_gap_ms=item.largest_gap_ms,
                coverage_percent=item.coverage_percent,
                error_message=item.error_message,
                started_at=item.started_at,
                completed_at=item.completed_at,
            )
            for item, driver in rows
        ]

    def get_coverage(
        self,
        race_session_id: UUID,
    ) -> SessionMapCoverageResponse:
        race_session = self.db.get(RaceSession, race_session_id)

        if race_session is None:
            raise SessionMapSourceSessionNotFoundError(
                "Race session not found."
            )

        eligible_driver_count = self.db.scalar(
            select(func.count(SessionResult.id)).where(
                SessionResult.race_session_id == race_session_id
            )
        ) or 0

        active_import = self.db.scalar(
            select(SessionMapImport)
            .where(
                SessionMapImport.race_session_id
                == race_session_id
            )
            .order_by(SessionMapImport.created_at.desc())
            .limit(1)
        )

        if active_import is None:
            return SessionMapCoverageResponse(
                race_session_id=race_session_id,
                active_map_import_id=None,
                active_map_import_status=None,
                sample_interval_ms=None,
                eligible_driver_count=eligible_driver_count,
                ready_driver_count=0,
                dataset_ready=False,
                full_session_track_map_ready=False,
                drivers=[],
            )

        drivers = self.list_drivers(active_import.id)

        ready_driver_count = sum(
            item.status == SessionMapDriverStatus.READY
            for item in drivers
        )

        dataset_ready = (
            active_import.status
            in {
                SessionMapImportStatus.COMPLETED,
                SessionMapImportStatus.PARTIAL,
            }
            and ready_driver_count > 0
        )

        full_session_track_map_ready = (
            active_import.requested_driver_numbers is None
            and active_import.status
            == SessionMapImportStatus.COMPLETED
            and ready_driver_count == eligible_driver_count
        )

        return SessionMapCoverageResponse(
            race_session_id=race_session_id,
            active_map_import_id=active_import.id,
            active_map_import_status=active_import.status,
            sample_interval_ms=active_import.sample_interval_ms,
            eligible_driver_count=eligible_driver_count,
            ready_driver_count=ready_driver_count,
            dataset_ready=dataset_ready,
            full_session_track_map_ready=(
                full_session_track_map_ready
            ),
            drivers=drivers,
        )

    def run(
        self,
        map_import_id: UUID,
    ) -> SessionMapImport:
        map_import = self.get(map_import_id)

        if map_import.status == SessionMapImportStatus.RUNNING:
            raise SessionMapImportStateError(
                "This session map import is already running."
            )

        if map_import.status == SessionMapImportStatus.COMPLETED:
            return map_import

        map_import.status = SessionMapImportStatus.RUNNING
        map_import.error_message = None
        map_import.completed_at = None

        if map_import.started_at is None:
            map_import.started_at = datetime.now(UTC)

        self.db.commit()

        try:
            race_session, meeting = self._load_source_context(
                map_import
            )

            fastf1_session = (
                self.fastf1_provider.load_telemetry_session(
                    year=meeting.year,
                    event_name=meeting.name,
                    session_identifier=(
                        race_session.session_identifier
                    ),
                )
            )

        except Exception as error:
            self._mark_job_failed(map_import.id, error)

            raise SessionMapImportError(
                "FastF1 could not load the session position feed."
            ) from error

        rows = self.db.execute(
            select(SessionMapImportDriver, Driver)
            .join(
                Driver,
                SessionMapImportDriver.driver_id == Driver.id,
            )
            .where(
                SessionMapImportDriver.map_import_id
                == map_import.id
            )
        ).all()

        rows.sort(
            key=lambda row: self._driver_sort_key(
                row[1].driver_number
            )
        )

        for item, driver in rows:
            if item.status in {
                SessionMapDriverStatus.READY,
                SessionMapDriverStatus.PARTIAL,
                SessionMapDriverStatus.SKIPPED,
            }:
                continue

            self._import_driver_map(
                map_import_id=map_import.id,
                race_session_id=race_session.id,
                driver=driver,
                item_id=item.id,
                fastf1_session=fastf1_session,
                sample_interval_ms=map_import.sample_interval_ms,
            )

            self._refresh_job_summary(map_import.id, final=False)

        self._refresh_job_summary(map_import.id, final=True)

        return self.get(map_import.id)

    def _load_source_context(
        self,
        map_import: SessionMapImport,
    ) -> tuple[RaceSession, Meeting]:
        context = self.db.execute(
            select(RaceSession, Meeting)
            .join(
                Meeting,
                RaceSession.meeting_id == Meeting.id,
            )
            .where(RaceSession.id == map_import.race_session_id)
        ).one_or_none()

        if context is None:
            raise SessionMapSourceSessionNotFoundError(
                "The source race session no longer exists."
            )

        return context

    def _import_driver_map(
        self,
        *,
        map_import_id: UUID,
        race_session_id: UUID,
        driver: Driver,
        item_id: UUID,
        fastf1_session,
        sample_interval_ms: int,
    ) -> None:
        item = self.db.get(SessionMapImportDriver, item_id)

        if item is None:
            return

        item.status = SessionMapDriverStatus.RUNNING
        item.error_message = None
        item.started_at = datetime.now(UTC)
        item.completed_at = None

        map_import = self.get(map_import_id)
        map_import.current_driver_number = driver.driver_number

        self.db.commit()

        try:
            driver_laps = fastf1_session.laps.pick_drivers(
                driver.driver_number
            )

            if driver_laps.empty:
                self._mark_driver_skipped(
                    item_id,
                    "FastF1 returned no laps for this driver.",
                )
                return

            positions = driver_laps.get_pos_data()

            if positions is None or positions.empty:
                self._mark_driver_skipped(
                    item_id,
                    "FastF1 returned no raw position data.",
                )
                return

            stored_laps = self.db.scalars(
                select(Lap)
                .where(
                    Lap.race_session_id == race_session_id,
                    Lap.driver_id == driver.id,
                )
                .order_by(Lap.lap_number)
            ).all()

            records, metrics = self._normalise_position_data(
                positions=positions,
                map_import_id=map_import_id,
                race_session_id=race_session_id,
                driver_id=driver.id,
                stored_laps=stored_laps,
                sample_interval_ms=sample_interval_ms,
            )

            if not records:
                self._mark_driver_skipped(
                    item_id,
                    "No usable X/Y position samples were found.",
                    raw_samples_seen=len(positions),
                    valid_samples=metrics["valid_samples"],
                )
                return

            self.db.execute(
                delete(SessionMapSample).where(
                    SessionMapSample.map_import_id == map_import_id,
                    SessionMapSample.driver_id == driver.id,
                )
            )

            for chunk in self._chunks(records, INSERT_CHUNK_SIZE):
                self.db.execute(
                    insert(SessionMapSample.__table__),
                    chunk,
                )

            item = self.db.get(SessionMapImportDriver, item_id)

            if item is None:
                raise RuntimeError("Map import driver item disappeared.")

            coverage_percent = metrics["coverage_percent"]
            largest_gap_ms = metrics["largest_gap_ms"]

            is_ready = (
                len(records) >= MAP_READY_MIN_SAMPLE_COUNT
                and coverage_percent
                >= MAP_READY_MIN_COVERAGE_PERCENT
                and largest_gap_ms <= MAP_READY_MAX_GAP_MS
            )

            item.status = (
                SessionMapDriverStatus.READY
                if is_ready
                else SessionMapDriverStatus.PARTIAL
            )

            item.raw_samples_seen = len(positions)
            item.valid_samples = metrics["valid_samples"]
            item.samples_written = len(records)
            item.first_sample_session_time_ms = metrics[
                "first_sample_session_time_ms"
            ]
            item.last_sample_session_time_ms = metrics[
                "last_sample_session_time_ms"
            ]
            item.largest_gap_ms = largest_gap_ms
            item.coverage_percent = coverage_percent
            item.completed_at = datetime.now(UTC)

            if not is_ready:
                item.error_message = (
                    "Position data was stored but did not meet the "
                    "full map-ready quality threshold."
                )

            self.db.commit()

        except Exception as error:
            self.db.rollback()

            item = self.db.get(SessionMapImportDriver, item_id)

            if item is not None:
                item.status = SessionMapDriverStatus.FAILED
                item.error_message = self._error_text(error)
                item.completed_at = datetime.now(UTC)

                self.db.commit()

    def _normalise_position_data(
        self,
        *,
        positions: pd.DataFrame,
        map_import_id: UUID,
        race_session_id: UUID,
        driver_id: UUID,
        stored_laps: list[Lap],
        sample_interval_ms: int,
    ) -> tuple[list[dict[str, object]], dict[str, object]]:
        candidates: list[dict[str, object]] = []

        for _, row in positions.iterrows():
            session_time_ms = self._timedelta_ms(
                row.get("SessionTime")
            )

            x = self._decimal(row.get("X"))
            y = self._decimal(row.get("Y"))

            if session_time_ms is None or x is None or y is None:
                continue

            candidates.append(
                {
                    "session_time_ms": session_time_ms,
                    "sampled_at": self._datetime(row.get("Date")),
                    "x": x,
                    "y": y,
                    "z": self._decimal(row.get("Z")),
                    "position_status": self._text(
                        row.get("Status")
                    ),
                    "sample_source": (
                        self._text(row.get("Source")) or "pos"
                    ),
                }
            )

        candidates.sort(
            key=lambda candidate: int(
                candidate["session_time_ms"]
            )
        )

        valid_samples = len(candidates)

        if not candidates:
            return [], {
                "valid_samples": 0,
                "first_sample_session_time_ms": None,
                "last_sample_session_time_ms": None,
                "largest_gap_ms": 0,
                "coverage_percent": Decimal("0"),
            }

        latest_by_bucket: dict[int, dict[str, object]] = {}

        for candidate in candidates:
            bucket = (
                int(candidate["session_time_ms"])
                // sample_interval_ms
            )
            latest_by_bucket[bucket] = candidate

        selected = [
            latest_by_bucket[key]
            for key in sorted(latest_by_bucket)
        ]

        starts, windows = self._build_lap_windows(stored_laps)

        records: list[dict[str, object]] = []

        for candidate in selected:
            session_time_ms = int(candidate["session_time_ms"])

            records.append(
                {
                    "id": uuid4(),
                    "map_import_id": map_import_id,
                    "race_session_id": race_session_id,
                    "driver_id": driver_id,
                    "lap_id": self._lap_id_for_time(
                        starts=starts,
                        windows=windows,
                        session_time_ms=session_time_ms,
                    ),
                    "session_time_ms": session_time_ms,
                    "sampled_at": candidate["sampled_at"],
                    "x": candidate["x"],
                    "y": candidate["y"],
                    "z": candidate["z"],
                    "position_status": candidate[
                        "position_status"
                    ],
                    "sample_source": candidate["sample_source"],
                }
            )

        raw_times = [
            int(candidate["session_time_ms"])
            for candidate in candidates
        ]

        largest_gap_ms = max(
            (
                current - previous
                for previous, current in zip(
                    raw_times,
                    raw_times[1:],
                )
            ),
            default=0,
        )

        expected_start_ms = windows[0][0] if windows else raw_times[0]
        expected_end_ms = windows[-1][1] if windows else raw_times[-1]

        expected_duration_ms = max(
            1,
            expected_end_ms - expected_start_ms,
        )

        observed_duration_ms = max(
            0,
            raw_times[-1] - raw_times[0],
        )

        coverage_percent = Decimal(
            str(
                round(
                    min(
                        100,
                        (
                            observed_duration_ms
                            / expected_duration_ms
                        )
                        * 100,
                    ),
                    2,
                )
            )
        )

        return records, {
            "valid_samples": valid_samples,
            "first_sample_session_time_ms": raw_times[0],
            "last_sample_session_time_ms": raw_times[-1],
            "largest_gap_ms": largest_gap_ms,
            "coverage_percent": coverage_percent,
        }

    @staticmethod
    def _build_lap_windows(
        laps: list[Lap],
    ) -> tuple[list[int], list[tuple[int, int, UUID]]]:
        usable_laps = [
            lap
            for lap in laps
            if lap.lap_start_time_ms is not None
        ]

        usable_laps.sort(
            key=lambda lap: (
                lap.lap_start_time_ms or 0,
                lap.lap_number,
            )
        )

        windows: list[tuple[int, int, UUID]] = []

        for index, lap in enumerate(usable_laps):
            start_ms = int(lap.lap_start_time_ms or 0)

            next_start_ms = (
                int(
                    usable_laps[index + 1].lap_start_time_ms
                    or start_ms
                )
                if index + 1 < len(usable_laps)
                else None
            )

            if lap.lap_time_ms is not None:
                end_ms = start_ms + lap.lap_time_ms
            elif next_start_ms is not None:
                end_ms = next_start_ms
            else:
                end_ms = start_ms

            windows.append((start_ms, end_ms, lap.id))

        return (
            [window[0] for window in windows],
            windows,
        )

    @staticmethod
    def _lap_id_for_time(
        *,
        starts: list[int],
        windows: list[tuple[int, int, UUID]],
        session_time_ms: int,
    ) -> UUID | None:
        if not starts:
            return None

        index = bisect_right(starts, session_time_ms) - 1

        if index < 0:
            return None

        start_ms, end_ms, lap_id = windows[index]

        if start_ms <= session_time_ms <= end_ms:
            return lap_id

        return None

    def _mark_driver_skipped(
        self,
        item_id: UUID,
        message: str,
        *,
        raw_samples_seen: int = 0,
        valid_samples: int = 0,
    ) -> None:
        item = self.db.get(SessionMapImportDriver, item_id)

        if item is None:
            return

        item.status = SessionMapDriverStatus.SKIPPED
        item.raw_samples_seen = raw_samples_seen
        item.valid_samples = valid_samples
        item.samples_written = 0
        item.error_message = message
        item.completed_at = datetime.now(UTC)

        self.db.commit()

    def _refresh_job_summary(
        self,
        map_import_id: UUID,
        *,
        final: bool,
    ) -> None:
        items = self.db.scalars(
            select(SessionMapImportDriver).where(
                SessionMapImportDriver.map_import_id == map_import_id
            )
        ).all()

        job = self.get(map_import_id)

        ready = sum(
            item.status == SessionMapDriverStatus.READY
            for item in items
        )
        partial = sum(
            item.status == SessionMapDriverStatus.PARTIAL
            for item in items
        )
        skipped = sum(
            item.status == SessionMapDriverStatus.SKIPPED
            for item in items
        )
        failed = sum(
            item.status == SessionMapDriverStatus.FAILED
            for item in items
        )

        processed = ready + partial + skipped + failed

        job.drivers_total = len(items)
        job.drivers_processed = processed
        job.drivers_completed = ready
        job.drivers_partial = partial
        job.drivers_skipped = skipped
        job.drivers_failed = failed
        job.samples_written = sum(
            item.samples_written
            for item in items
        )

        if final:
            job.progress_percentage = 100
            job.current_driver_number = None
            job.completed_at = datetime.now(UTC)

            if ready == len(items):
                job.status = SessionMapImportStatus.COMPLETED
            elif ready + partial > 0:
                job.status = SessionMapImportStatus.PARTIAL
            else:
                job.status = SessionMapImportStatus.FAILED

        elif job.drivers_total:
            job.progress_percentage = int(
                round(
                    processed / job.drivers_total * 100
                )
            )

        self.db.commit()

    def _mark_job_failed(
        self,
        map_import_id: UUID,
        error: Exception,
    ) -> None:
        self.db.rollback()

        job = self.db.get(SessionMapImport, map_import_id)

        if job is None:
            return

        job.status = SessionMapImportStatus.FAILED
        job.error_message = self._error_text(error)
        job.completed_at = datetime.now(UTC)
        job.current_driver_number = None

        self.db.commit()

    @staticmethod
    def _chunks(
        values: list[dict[str, object]],
        size: int,
    ):
        for start in range(0, len(values), size):
            yield values[start:start + size]

    @staticmethod
    def _text(value: object) -> str | None:
        if value is None:
            return None

        try:
            if bool(pd.isna(value)):
                return None
        except (TypeError, ValueError):
            pass

        text = str(value).strip()

        if not text or text.lower() in {
            "nan",
            "nat",
            "none",
        }:
            return None

        return text

    @staticmethod
    def _decimal(value: object) -> Decimal | None:
        text = SessionMapImportService._text(value)

        if text is None:
            return None

        try:
            decimal_value = Decimal(text)

            if not decimal_value.is_finite():
                return None

            return decimal_value

        except InvalidOperation:
            return None

    @staticmethod
    def _timedelta_ms(value: object) -> int | None:
        if value is None:
            return None

        try:
            delta = pd.Timedelta(value)

            if pd.isna(delta):
                return None

            return int(round(delta.total_seconds() * 1000))

        except (TypeError, ValueError):
            return None

    @staticmethod
    def _datetime(value: object) -> datetime | None:
        if value is None:
            return None

        try:
            if bool(pd.isna(value)):
                return None
        except (TypeError, ValueError):
            pass

        if isinstance(value, pd.Timestamp):
            value = value.to_pydatetime()

        if not isinstance(value, datetime):
            return None

        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)

        return value

    @staticmethod
    def _driver_sort_key(
        driver_number: str,
    ) -> tuple[int, str]:
        if driver_number.isdigit():
            return int(driver_number), driver_number

        return 9999, driver_number

    @staticmethod
    def _error_text(error: Exception) -> str:
        return f"{type(error).__name__}: {error}"[:2_000]