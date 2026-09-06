from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.driver import Driver
from app.models.lap import Lap
from app.models.meeting import Meeting
from app.models.race_control_event import RaceControlEvent
from app.models.race_session import RaceSession
from app.models.weather_sample import WeatherSample
from app.providers.fastf1_provider import FastF1Provider
from app.schemas.race_context import (
    PitEventResponse,
    RaceContextImportResponse,
    RaceControlEventResponse,
    SessionTimelineEventResponse,
    SessionTimelineResponse,
    TimelineEventType,
    WeatherSampleResponse,
)

FASTF1_SOURCE = "FASTF1"

UNPAIRED_PIT_FLAGS = {
    "OUT_ONLY",
    "IN_ONLY",
    "DUPLICATE_OR_OVERLAPPING_ENTRY",
    "NON_POSITIVE_DURATION",
}


class RaceContextSessionNotFoundError(LookupError):
    pass


class RaceContextImportError(RuntimeError):
    pass


class RaceContextValidationError(ValueError):
    pass


class RaceContextService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.fastf1_provider = FastF1Provider()

    def import_context(
        self,
        race_session_id: UUID,
        *,
        loaded_session=None,
    ) -> RaceContextImportResponse:
        race_session, meeting = self._load_source_context(race_session_id)

        try:
            fastf1_session = loaded_session
            if fastf1_session is None:
                fastf1_session = self.fastf1_provider.load_context_session(
                    year=meeting.year,
                    event_name=meeting.name,
                    session_identifier=race_session.session_identifier,
                )
        except Exception as error:
            raise RaceContextImportError(
                "FastF1 could not load weather and race-control data."
            ) from error

        session_clock_anchor = self._get_session_clock_anchor(race_session_id)

        weather_samples = self._normalise_weather(
            race_session_id=race_session_id,
            fastf1_session=fastf1_session,
            session_clock_anchor=session_clock_anchor,
        )

        race_control_events = self._normalise_race_control_events(
            race_session_id=race_session_id,
            fastf1_session=fastf1_session,
            session_clock_anchor=session_clock_anchor,
        )

        try:
            # Re-import is safe: replace only FASTF1 context for this session.
            self.db.execute(
                delete(WeatherSample).where(
                    WeatherSample.race_session_id == race_session_id,
                    WeatherSample.source == FASTF1_SOURCE,
                )
            )

            self.db.execute(
                delete(RaceControlEvent).where(
                    RaceControlEvent.race_session_id == race_session_id,
                    RaceControlEvent.source == FASTF1_SOURCE,
                )
            )

            self.db.add_all(weather_samples)
            self.db.add_all(race_control_events)
            self.db.commit()

        except Exception as error:
            self.db.rollback()

            raise RaceContextImportError(
                "Race context data could not be saved."
            ) from error

        pit_events_available = len(self._build_pit_events(race_session_id))

        aligned = session_clock_anchor is not None

        return RaceContextImportResponse(
            race_session_id=race_session_id,
            source=FASTF1_SOURCE,
            weather_samples_imported=len(weather_samples),
            race_control_events_imported=len(race_control_events),
            pit_events_available=pit_events_available,
            session_clock_alignment=(
                "LAP_ANCHORED" if aligned else "UNALIGNED"
            ),
            session_clock_anchor_at=session_clock_anchor,
            warning=(
                None
                if aligned
                else (
                    "Race-control messages retain their UTC timestamps, "
                    "but replay-clock alignment needs imported lap data."
                )
            ),
        )

    def list_weather(
        self,
        *,
        race_session_id: UUID,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> list[WeatherSampleResponse]:
        self._require_session(race_session_id)
        self._validate_window(start_ms, end_ms)

        statement = (
            select(WeatherSample)
            .where(
                WeatherSample.race_session_id == race_session_id,
                WeatherSample.source == FASTF1_SOURCE,
            )
            .order_by(WeatherSample.session_time_ms)
        )

        if start_ms is not None:
            statement = statement.where(
                WeatherSample.session_time_ms >= start_ms
            )

        if end_ms is not None:
            statement = statement.where(
                WeatherSample.session_time_ms <= end_ms
            )

        samples = self.db.scalars(statement).all()

        return [self._to_weather_response(sample) for sample in samples]

    def list_race_control(
        self,
        *,
        race_session_id: UUID,
        driver_number: str | None = None,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> list[RaceControlEventResponse]:
        self._require_session(race_session_id)
        self._validate_window(start_ms, end_ms)

        statement = (
            select(RaceControlEvent)
            .where(
                RaceControlEvent.race_session_id == race_session_id,
                RaceControlEvent.source == FASTF1_SOURCE,
            )
            .order_by(
                RaceControlEvent.session_time_ms.is_(None),
                RaceControlEvent.session_time_ms,
                RaceControlEvent.occurred_at,
                RaceControlEvent.id,
            )
        )

        if driver_number is not None:
            statement = statement.where(
                RaceControlEvent.driver_number == driver_number.strip()
            )

        if start_ms is not None:
            statement = statement.where(
                RaceControlEvent.session_time_ms >= start_ms
            )

        if end_ms is not None:
            statement = statement.where(
                RaceControlEvent.session_time_ms <= end_ms
            )

        events = self.db.scalars(statement).all()

        return [self._to_race_control_response(event) for event in events]

    def list_pit_events(
        self,
        *,
        race_session_id: UUID,
        driver_number: str | None = None,
        include_unpaired: bool = True,
    ) -> list[PitEventResponse]:
        self._require_session(race_session_id)

        return self._build_pit_events(
            race_session_id,
            driver_number=driver_number,
            include_unpaired=include_unpaired,
        )

    def get_timeline(
        self,
        *,
        race_session_id: UUID,
        event_types: set[TimelineEventType] | None = None,
        driver_number: str | None = None,
        start_ms: int | None = None,
        end_ms: int | None = None,
        include_weather: bool = False,
        limit: int = 500,
        offset: int = 0,
    ) -> SessionTimelineResponse:
        self._require_session(race_session_id)
        self._validate_window(start_ms, end_ms)

        selected_types = event_types or {
            TimelineEventType.RACE_CONTROL,
            TimelineEventType.PIT_ENTRY,
            TimelineEventType.PIT_EXIT,
        }

        if include_weather:
            selected_types.add(TimelineEventType.WEATHER)

        timeline: list[SessionTimelineEventResponse] = []

        if TimelineEventType.RACE_CONTROL in selected_types:
            for event in self.list_race_control(
                race_session_id=race_session_id,
                driver_number=driver_number,
                start_ms=start_ms,
                end_ms=end_ms,
            ):
                timeline.append(self._timeline_from_race_control(event))

        if (
            TimelineEventType.PIT_ENTRY in selected_types
            or TimelineEventType.PIT_EXIT in selected_types
        ):
            for pit_event in self.list_pit_events(
                race_session_id=race_session_id,
                driver_number=driver_number,
            ):
                if pit_event.event_type not in selected_types:
                    continue

                if not self._within_window(
                    pit_event.session_time_ms,
                    start_ms,
                    end_ms,
                ):
                    continue

                timeline.append(self._timeline_from_pit(pit_event))

        if TimelineEventType.WEATHER in selected_types:
            for sample in self.list_weather(
                race_session_id=race_session_id,
                start_ms=start_ms,
                end_ms=end_ms,
            ):
                timeline.append(self._timeline_from_weather(sample))

        timeline.sort(key=self._timeline_sort_key)

        total = len(timeline)
        paged_events = timeline[offset : offset + limit]

        session_clock_anchor = self._get_session_clock_anchor(race_session_id)

        warnings: list[str] = []

        if session_clock_anchor is None:
            warnings.append(
                "Race-control messages are stored in UTC, but replay "
                "alignment requires imported lap data."
            )

        if not timeline:
            warnings.append(
                "No matching context data exists. Run the context import "
                "endpoint first."
            )

        return SessionTimelineResponse(
            race_session_id=race_session_id,
            session_clock_alignment=(
                "LAP_ANCHORED"
                if session_clock_anchor is not None
                else "UNALIGNED"
            ),
            total=total,
            events=paged_events,
            warnings=warnings,
        )

    def _load_source_context(
        self,
        race_session_id: UUID,
    ) -> tuple[RaceSession, Meeting]:
        row = self.db.execute(
            select(RaceSession, Meeting)
            .join(Meeting, RaceSession.meeting_id == Meeting.id)
            .where(RaceSession.id == race_session_id)
        ).one_or_none()

        if row is None:
            raise RaceContextSessionNotFoundError("Race session not found.")

        race_session, meeting = row

        return race_session, meeting

    def _require_session(self, race_session_id: UUID) -> RaceSession:
        race_session = self.db.get(RaceSession, race_session_id)

        if race_session is None:
            raise RaceContextSessionNotFoundError("Race session not found.")

        return race_session

    def _get_session_clock_anchor(
        self,
        race_session_id: UUID,
    ) -> datetime | None:
        row = self.db.execute(
            select(
                Lap.lap_start_at,
                Lap.lap_start_time_ms,
            )
            .where(
                Lap.race_session_id == race_session_id,
                Lap.lap_start_at.is_not(None),
                Lap.lap_start_time_ms.is_not(None),
            )
            .order_by(Lap.lap_start_time_ms)
            .limit(1)
        ).first()

        if row is None:
            return None

        lap_start_at, lap_start_time_ms = row
        absolute_start = self._as_utc(lap_start_at)

        if absolute_start is None or lap_start_time_ms is None:
            return None

        return absolute_start - timedelta(milliseconds=int(lap_start_time_ms))

    def _normalise_weather(
        self,
        *,
        race_session_id: UUID,
        fastf1_session,
        session_clock_anchor: datetime | None,
    ) -> list[WeatherSample]:
        weather_data = getattr(
            fastf1_session,
            "weather_data",
            None,
        )

        if weather_data is None or weather_data.empty:
            return []

        samples_by_time: dict[int, WeatherSample] = {}

        for _, row in weather_data.iterrows():
            session_time_ms = self._timedelta_ms(row.get("Time"))

            if session_time_ms is None:
                continue

            occurred_at = (
                session_clock_anchor + timedelta(milliseconds=session_time_ms)
                if session_clock_anchor is not None
                else None
            )

            samples_by_time[session_time_ms] = WeatherSample(
                race_session_id=race_session_id,
                source=FASTF1_SOURCE,
                session_time_ms=session_time_ms,
                occurred_at=occurred_at,
                air_temperature_c=self._decimal(row.get("AirTemp")),
                track_temperature_c=self._decimal(row.get("TrackTemp")),
                humidity_percent=self._decimal(row.get("Humidity")),
                pressure_hpa=self._decimal(row.get("Pressure")),
                rainfall=self._boolean(row.get("Rainfall")),
                wind_speed_mps=self._decimal(row.get("WindSpeed")),
                wind_direction_deg=self._integer(row.get("WindDirection")),
            )

        return [samples_by_time[time] for time in sorted(samples_by_time)]

    def _normalise_race_control_events(
        self,
        *,
        race_session_id: UUID,
        fastf1_session,
        session_clock_anchor: datetime | None,
    ) -> list[RaceControlEvent]:
        messages = getattr(
            fastf1_session,
            "race_control_messages",
            None,
        )

        if messages is None or messages.empty:
            return []

        events: list[RaceControlEvent] = []

        for _, row in messages.iterrows():
            occurred_at = self._as_utc(row.get("Time"))
            message = self._text(row.get("Message"))

            if occurred_at is None or message is None:
                continue

            session_time_ms = self._session_time_ms(
                occurred_at,
                session_clock_anchor,
            )

            events.append(
                RaceControlEvent(
                    race_session_id=race_session_id,
                    source=FASTF1_SOURCE,
                    occurred_at=occurred_at,
                    session_time_ms=session_time_ms,
                    category=self._text(row.get("Category")),
                    message=message,
                    status=self._text(row.get("Status")),
                    flag=self._text(row.get("Flag")),
                    scope=self._text(row.get("Scope")),
                    sector_number=self._integer(row.get("Sector")),
                    driver_number=self._text(row.get("RacingNumber")),
                    lap_number=self._integer(row.get("Lap")),
                )
            )

        return events

    def _build_pit_events(
        self,
        race_session_id: UUID,
        *,
        driver_number: str | None = None,
        include_unpaired: bool = True,
    ) -> list[PitEventResponse]:
        statement = (
            select(Lap, Driver)
            .join(Driver, Lap.driver_id == Driver.id)
            .where(Lap.race_session_id == race_session_id)
            .order_by(Driver.driver_number, Lap.lap_number)
        )

        if driver_number is not None:
            statement = statement.where(
                Driver.driver_number == driver_number.strip()
            )

        rows = self.db.execute(statement).all()
        session_clock_anchor = self._get_session_clock_anchor(race_session_id)

        grouped: dict[UUID, tuple[Driver, list[Lap]]] = {}

        for lap, driver in rows:
            if driver.id not in grouped:
                grouped[driver.id] = (driver, [])

            grouped[driver.id][1].append(lap)

        drafts: list[dict[str, Any]] = []

        for driver, laps in grouped.values():
            candidates: list[dict[str, Any]] = []

            for lap in laps:
                if lap.pit_in_time_ms is not None:
                    candidates.append(
                        self._pit_event_draft(
                            lap=lap,
                            driver=driver,
                            event_type=TimelineEventType.PIT_ENTRY,
                            session_time_ms=lap.pit_in_time_ms,
                            session_clock_anchor=session_clock_anchor,
                        )
                    )

                if lap.pit_out_time_ms is not None:
                    candidates.append(
                        self._pit_event_draft(
                            lap=lap,
                            driver=driver,
                            event_type=TimelineEventType.PIT_EXIT,
                            session_time_ms=lap.pit_out_time_ms,
                            session_clock_anchor=session_clock_anchor,
                        )
                    )

            candidates.sort(key=self._pit_event_sort_key)

            active_entry: dict[str, Any] | None = None

            for event in candidates:
                if event["event_type"] == TimelineEventType.PIT_ENTRY:
                    if active_entry is not None:
                        active_entry["data_quality_flags"].append(
                            "DUPLICATE_OR_OVERLAPPING_ENTRY"
                        )
                        event["data_quality_flags"].append(
                            "DUPLICATE_OR_OVERLAPPING_ENTRY"
                        )

                    active_entry = event
                    continue

                if active_entry is None:
                    event["data_quality_flags"].append("OUT_ONLY")
                    continue

                duration_ms = (
                    event["session_time_ms"] - active_entry["session_time_ms"]
                )

                active_entry["paired_event_id"] = event["event_id"]
                event["paired_event_id"] = active_entry["event_id"]

                if duration_ms > 0:
                    active_entry["pit_lane_duration_ms"] = duration_ms
                    event["pit_lane_duration_ms"] = duration_ms
                else:
                    active_entry["data_quality_flags"].append(
                        "NON_POSITIVE_DURATION"
                    )
                    event["data_quality_flags"].append("NON_POSITIVE_DURATION")

                active_entry = None

            if active_entry is not None:
                active_entry["data_quality_flags"].append("IN_ONLY")

            drafts.extend(candidates)

        if not include_unpaired:
            drafts = [
                draft
                for draft in drafts
                if not (set(draft["data_quality_flags"]) & UNPAIRED_PIT_FLAGS)
            ]

        drafts.sort(key=self._pit_event_sort_key)

        return [PitEventResponse(**draft) for draft in drafts]

    def _pit_event_draft(
        self,
        *,
        lap: Lap,
        driver: Driver,
        event_type: TimelineEventType,
        session_time_ms: int,
        session_clock_anchor: datetime | None,
    ) -> dict[str, Any]:
        action = (
            "entry" if event_type == TimelineEventType.PIT_ENTRY else "exit"
        )

        occurred_at = self._pit_occurred_at(
            lap=lap,
            session_time_ms=session_time_ms,
            session_clock_anchor=session_clock_anchor,
        )

        flags: list[str] = []

        if occurred_at is None:
            flags.append("ABSOLUTE_TIME_UNAVAILABLE")

        return {
            "event_id": f"pit-{action}:{lap.id}",
            "event_type": event_type,
            "session_time_ms": int(session_time_ms),
            "occurred_at": occurred_at,
            "driver_number": driver.driver_number,
            "abbreviation": driver.abbreviation,
            "driver_name": self._driver_name(driver),
            "lap_id": lap.id,
            "lap_number": lap.lap_number,
            "paired_event_id": None,
            "pit_lane_duration_ms": None,
            "data_quality_flags": flags,
        }

    def _pit_occurred_at(
        self,
        *,
        lap: Lap,
        session_time_ms: int,
        session_clock_anchor: datetime | None,
    ) -> datetime | None:
        lap_start_at = self._as_utc(lap.lap_start_at)

        if lap_start_at is not None and lap.lap_start_time_ms is not None:
            return lap_start_at + timedelta(
                milliseconds=(session_time_ms - lap.lap_start_time_ms)
            )

        if session_clock_anchor is not None:
            return session_clock_anchor + timedelta(
                milliseconds=session_time_ms
            )

        return None

    def _to_weather_response(
        self,
        sample: WeatherSample,
    ) -> WeatherSampleResponse:
        return WeatherSampleResponse(
            id=sample.id,
            source=sample.source,
            session_time_ms=sample.session_time_ms,
            occurred_at=sample.occurred_at,
            air_temperature_c=self._float(sample.air_temperature_c),
            track_temperature_c=self._float(sample.track_temperature_c),
            humidity_percent=self._float(sample.humidity_percent),
            pressure_hpa=self._float(sample.pressure_hpa),
            rainfall=sample.rainfall,
            wind_speed_mps=self._float(sample.wind_speed_mps),
            wind_direction_deg=sample.wind_direction_deg,
        )

    @staticmethod
    def _to_race_control_response(
        event: RaceControlEvent,
    ) -> RaceControlEventResponse:
        return RaceControlEventResponse(
            id=event.id,
            source=event.source,
            occurred_at=event.occurred_at,
            session_time_ms=event.session_time_ms,
            category=event.category,
            message=event.message,
            status=event.status,
            flag=event.flag,
            scope=event.scope,
            sector_number=event.sector_number,
            driver_number=event.driver_number,
            lap_number=event.lap_number,
        )

    def _timeline_from_race_control(
        self,
        event: RaceControlEventResponse,
    ) -> SessionTimelineEventResponse:
        title = "Race control"

        if event.flag is not None:
            title = f"{event.flag.title()} flag"
        elif event.category is not None:
            title = f"Race control: {event.category}"

        return SessionTimelineEventResponse(
            event_id=f"race-control:{event.id}",
            event_type=TimelineEventType.RACE_CONTROL,
            session_time_ms=event.session_time_ms,
            occurred_at=event.occurred_at,
            priority=10,
            title=title,
            message=event.message,
            severity=self._race_control_severity(event),
            driver_number=event.driver_number,
            lap_number=event.lap_number,
            data_quality_flags=(
                []
                if event.session_time_ms is not None
                else ["REPLAY_TIME_UNALIGNED"]
            ),
            payload={
                "category": event.category,
                "status": event.status,
                "flag": event.flag,
                "scope": event.scope,
                "sector_number": event.sector_number,
            },
        )

    @staticmethod
    def _timeline_from_pit(
        event: PitEventResponse,
    ) -> SessionTimelineEventResponse:
        action = (
            "entered"
            if event.event_type == TimelineEventType.PIT_ENTRY
            else "exited"
        )

        return SessionTimelineEventResponse(
            event_id=event.event_id,
            event_type=event.event_type,
            session_time_ms=event.session_time_ms,
            occurred_at=event.occurred_at,
            priority=20,
            title=(
                f"{event.driver_number} pit "
                f"{'entry' if action == 'entered' else 'exit'}"
            ),
            message=(
                f"{event.driver_name} {action} the pit lane "
                f"on lap {event.lap_number}."
            ),
            severity="INFO",
            driver_number=event.driver_number,
            lap_number=event.lap_number,
            data_quality_flags=event.data_quality_flags,
            payload={
                "abbreviation": event.abbreviation,
                "lap_id": str(event.lap_id),
                "paired_event_id": event.paired_event_id,
                "pit_lane_duration_ms": event.pit_lane_duration_ms,
            },
        )

    @staticmethod
    def _timeline_from_weather(
        sample: WeatherSampleResponse,
    ) -> SessionTimelineEventResponse:
        parts: list[str] = []

        if sample.track_temperature_c is not None:
            parts.append(f"Track {sample.track_temperature_c:.1f}°C")

        if sample.air_temperature_c is not None:
            parts.append(f"Air {sample.air_temperature_c:.1f}°C")

        if sample.rainfall is True:
            parts.append("Rain detected")

        message = "; ".join(parts) or "Weather sample recorded."

        return SessionTimelineEventResponse(
            event_id=f"weather:{sample.id}",
            event_type=TimelineEventType.WEATHER,
            session_time_ms=sample.session_time_ms,
            occurred_at=sample.occurred_at,
            priority=30,
            title="Weather update",
            message=message,
            severity="INFO",
            data_quality_flags=(
                []
                if sample.occurred_at is not None
                else ["ABSOLUTE_TIME_UNAVAILABLE"]
            ),
            payload={
                "air_temperature_c": sample.air_temperature_c,
                "track_temperature_c": sample.track_temperature_c,
                "humidity_percent": sample.humidity_percent,
                "pressure_hpa": sample.pressure_hpa,
                "rainfall": sample.rainfall,
                "wind_speed_mps": sample.wind_speed_mps,
                "wind_direction_deg": sample.wind_direction_deg,
            },
        )

    @staticmethod
    def _race_control_severity(
        event: RaceControlEventResponse,
    ) -> str:
        text = " ".join(
            value
            for value in [
                event.flag,
                event.category,
                event.status,
                event.message,
            ]
            if value
        ).upper()

        if "RED" in text or "SESSION STOPPED" in text:
            return "CRITICAL"

        if "YELLOW" in text or "SAFETY CAR" in text or "VSC" in text:
            return "WARNING"

        return "INFO"

    @staticmethod
    def _timeline_sort_key(
        event: SessionTimelineEventResponse,
    ) -> tuple[int, int, int, str]:
        if event.session_time_ms is not None:
            return (
                0,
                event.session_time_ms,
                event.priority,
                event.event_id,
            )

        if event.occurred_at is not None:
            return (
                1,
                int(event.occurred_at.timestamp() * 1000),
                event.priority,
                event.event_id,
            )

        return (2, 0, event.priority, event.event_id)

    @staticmethod
    def _pit_event_sort_key(
        event: dict[str, Any],
    ) -> tuple[int, int, str, str]:
        type_rank = (
            0 if event["event_type"] == TimelineEventType.PIT_ENTRY else 1
        )

        return (
            int(event["session_time_ms"]),
            type_rank,
            str(event["driver_number"]),
            str(event["event_id"]),
        )

    @staticmethod
    def _within_window(
        session_time_ms: int | None,
        start_ms: int | None,
        end_ms: int | None,
    ) -> bool:
        if start_ms is None and end_ms is None:
            return True

        if session_time_ms is None:
            return False

        if start_ms is not None and session_time_ms < start_ms:
            return False

        return end_ms is None or session_time_ms <= end_ms

    @staticmethod
    def _validate_window(
        start_ms: int | None,
        end_ms: int | None,
    ) -> None:
        if start_ms is not None and end_ms is not None and start_ms > end_ms:
            raise RaceContextValidationError(
                "start_ms cannot be greater than end_ms."
            )

    @staticmethod
    def _session_time_ms(
        occurred_at: datetime,
        session_clock_anchor: datetime | None,
    ) -> int | None:
        if session_clock_anchor is None:
            return None

        return int(
            round((occurred_at - session_clock_anchor).total_seconds() * 1000)
        )

    @staticmethod
    def _driver_name(driver: Driver) -> str:
        return (
            driver.full_name
            or " ".join(
                part
                for part in [
                    driver.first_name,
                    driver.last_name,
                ]
                if part
            )
            or driver.abbreviation
            or driver.driver_number
        )

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

        if not text or text.lower() in {"nan", "nat", "none"}:
            return None

        return text

    @staticmethod
    def _integer(value: object) -> int | None:
        text = RaceContextService._text(value)

        if text is None:
            return None

        try:
            return int(float(text))
        except ValueError:
            return None

    @staticmethod
    def _decimal(value: object) -> Decimal | None:
        text = RaceContextService._text(value)

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
    def _boolean(value: object) -> bool | None:
        if value is None:
            return None

        try:
            if bool(pd.isna(value)):
                return None
        except (TypeError, ValueError):
            pass

        if isinstance(value, bool):
            return value

        normalized = str(value).strip().lower()

        if normalized in {"true", "1", "yes"}:
            return True

        if normalized in {"false", "0", "no"}:
            return False

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
    def _as_utc(value: object) -> datetime | None:
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

        return value.astimezone(UTC)

    @staticmethod
    def _float(value: Decimal | None) -> float | None:
        return float(value) if value is not None else None
