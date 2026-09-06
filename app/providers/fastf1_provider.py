from datetime import datetime
import logging
from pathlib import Path

import fastf1
import pandas as pd
import urllib3
from urllib3.exceptions import InsecureRequestWarning

from app.core.config import settings
from app.providers.base_provider import (
    DriverPreview,
    ProviderError,
    SessionPreview,
    TimingDataProvider,
)
from app.schemas.weekend import WeekendSchedule, WeekendSessionSchedule


logger = logging.getLogger(__name__)


class FastF1Provider(TimingDataProvider):
    source_name = "FASTF1"

    def __init__(self) -> None:
        cache_path = Path(settings.fastf1_cache_path)
        cache_path.mkdir(parents=True, exist_ok=True)

        fastf1.Cache.enable_cache(str(cache_path))
        self._configure_ssl_verification()

    def get_weekend_schedule(
        self,
        *,
        year: int,
        event_name: str,
    ) -> WeekendSchedule:
        """Resolve a location/name against the schedule, without loading data."""
        try:
            schedule = fastf1.get_event_schedule(year, include_testing=False)
        except Exception as error:
            raise ProviderError("The race schedule is unavailable.") from error
        query = " ".join(event_name.casefold().split())
        matches = []
        alias_counts: dict[str, int] = {}
        for _, event in schedule.iterrows():
            aliases = {
                " ".join(str(event.get(field, "")).casefold().split())
                for field in (
                    "EventName",
                    "OfficialEventName",
                    "Location",
                    "Country",
                )
            }
            aliases.add(str(int(event["RoundNumber"])))
            for alias in aliases:
                alias_counts[alias] = alias_counts.get(alias, 0) + 1
            if query in aliases:
                matches.append((event, aliases))
        if len(matches) != 1:
            raise ValueError(
                "Select an exact event name, circuit location or round number "
                "from this season."
            )
        event, aliases = matches[0]
        identifiers = {
            "Practice 1": "FP1",
            "Practice 2": "FP2",
            "Practice 3": "FP3",
            "Qualifying": "Q",
            "Race": "R",
            "Sprint": "S",
            "Sprint Shootout": "SS",
            "Sprint Qualifying": "SQ",
        }
        sessions = []
        for index in range(1, 6):
            name = self._text(event.get(f"Session{index}"))
            if name is None:
                continue
            scheduled_at = event.get(f"Session{index}DateUtc")
            if pd.isna(scheduled_at):
                scheduled_at = None
            elif scheduled_at is not None:
                scheduled_at = pd.Timestamp(scheduled_at)
                if scheduled_at.tzinfo is None:
                    scheduled_at = scheduled_at.tz_localize("UTC")
                scheduled_at = scheduled_at.to_pydatetime()
            sessions.append(
                WeekendSessionSchedule(
                    identifier=identifiers.get(name, name),
                    name=name,
                    scheduled_at=scheduled_at,
                )
            )
        if not sessions:
            raise ValueError("This weekend has no scheduled sessions.")
        return WeekendSchedule(
            year=year,
            round_number=int(event["RoundNumber"]),
            event_name=str(event["EventName"]),
            aliases=sorted(
                alias for alias in aliases - {"", "nan", "none"}
                if alias_counts[alias] == 1
            ),
            sessions=sessions,
        )

    def load_full_session(
        self,
        *,
        year: int,
        round_number: int,
        session_identifier: str,
    ):
        try:
            session = fastf1.get_session(
                year, round_number, session_identifier
            )
            session.load(
                laps=True, telemetry=True, weather=True, messages=True
            )
            return session
        except Exception as error:
            raise ProviderError(
                "Full session data could not be loaded."
            ) from error

    def get_session_preview(
        self,
        *,
        year: int,
        event_name: str,
        session_identifier: str,
    ) -> SessionPreview:
        try:
            session = fastf1.get_session(
                year,
                event_name,
                session_identifier,
            )

            session.load(
                telemetry=False,
                laps=False,
                weather=False,
                messages=False,
            )
        except Exception as error:
            raise ProviderError(
                f"FastF1 could not load {event_name} {year} "
                f"({session_identifier})."
            ) from error

        event = session.event
        drivers = self._extract_drivers(session.results)

        return SessionPreview(
            source=self.source_name,
            year=year,
            meeting_name=self._text(event.get("EventName")) or event_name,
            official_meeting_name=self._text(event.get("OfficialEventName")),
            session_name=self._text(session.name) or session_identifier,
            session_identifier=session_identifier,
            event_date=self._datetime(event.get("EventDate")),
            country_name=self._text(event.get("Country")),
            location=self._text(event.get("Location")),
            drivers=tuple(drivers),
        )

    def load_context_session(
        self,
        *,
        year: int,
        event_name: str,
        session_identifier: str,
    ):
        try:
            session = fastf1.get_session(
                year,
                event_name,
                session_identifier,
            )

            session.load(
                telemetry=False,
                laps=True,
                weather=True,
                messages=True,
            )

            return session

        except Exception as error:
            raise ProviderError(
                f"FastF1 could not load context data for {event_name} "
                f"{year} ({session_identifier})."
            ) from error

    def _extract_drivers(self, results: pd.DataFrame) -> list[DriverPreview]:
        drivers: list[DriverPreview] = []

        for _, result in results.iterrows():
            driver_number = self._text(result.get("DriverNumber"))

            if driver_number is None:
                continue

            drivers.append(
                DriverPreview(
                    driver_number=driver_number,
                    abbreviation=self._text(result.get("Abbreviation")),
                    full_name=self._text(result.get("FullName")),
                    team_name=self._text(result.get("TeamName")),
                    team_colour=self._text(result.get("TeamColor")),
                    country_code=self._text(result.get("CountryCode")),
                    classified_position=self._text(
                        result.get("ClassifiedPosition")
                    ),
                )
            )

        return sorted(
            drivers,
            key=lambda driver: (
                int(driver.driver_number)
                if driver.driver_number.isdigit()
                else 999
            ),
        )

    def _configure_ssl_verification(self) -> None:
        if settings.fastf1_verify_ssl:
            return

        if settings.environment.lower() not in {"development", "test"}:
            raise ProviderError(
                "FASTF1_VERIFY_SSL=false is only allowed in development or test."
            )

        # FastF1 uses internal requests sessions for its HTTP/cache layer.
        # This temporary development workaround avoids your local certificate issue.
        fastf1.Cache._requests_session.verify = False

        if fastf1.Cache._requests_session_cached is not None:
            fastf1.Cache._requests_session_cached.verify = False

        urllib3.disable_warnings(InsecureRequestWarning)

        logger.warning(
            "FastF1 SSL certificate verification is disabled for local development."
        )

    def load_laps_session(
        self,
        *,
        year: int,
        event_name: str,
        session_identifier: str,
    ):
        try:
            session = fastf1.get_session(
                year,
                event_name,
                session_identifier,
            )

            session.load(
                telemetry=False,
                laps=True,
                weather=False,
                messages=False,
            )

            return session
        except Exception as error:
            raise ProviderError(
                f"FastF1 could not load lap data for {event_name} {year} "
                f"({session_identifier})."
            ) from error

    def load_telemetry_session(
        self,
        *,
        year: int,
        event_name: str,
        session_identifier: str,
    ):
        try:
            session = fastf1.get_session(
                year,
                event_name,
                session_identifier,
            )

            session.load(
                telemetry=True,
                laps=True,
                weather=False,
                messages=False,
            )

            return session
        except Exception as error:
            raise ProviderError(
                f"FastF1 could not load telemetry for {event_name} {year} "
                f"({session_identifier})."
            ) from error

    def load_results_session(
        self,
        *,
        year: int,
        event_name: str,
        session_identifier: str,
    ):
        try:
            session = fastf1.get_session(
                year,
                event_name,
                session_identifier,
            )

            session.load(
                telemetry=False,
                laps=False,
                weather=False,
                messages=False,
            )

            return session
        except Exception as error:
            raise ProviderError(
                f"FastF1 could not load {event_name} {year} "
                f"({session_identifier})."
            ) from error

    @staticmethod
    def _canonical_session_type(session_type: str) -> str:
        aliases = {
            "R": "Race",
            "RACE": "Race",
            "Q": "Qualifying",
            "QUALIFYING": "Qualifying",
            "FP1": "Practice 1",
            "FP2": "Practice 2",
            "FP3": "Practice 3",
            "S": "Sprint",
            "SPRINT": "Sprint",
        }

        normalized = session_type.strip().upper()

        return aliases.get(normalized, session_type.strip())

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
    def _text(value: object) -> str | None:
        if value is None:
            return None

        try:
            if pd.isna(value):
                return None
        except TypeError:
            pass

        text = str(value).strip()

        if not text or text.lower() in {"nan", "nat", "none"}:
            return None

        return text

    @staticmethod
    def _datetime(value: object) -> datetime | None:
        if value is None:
            return None

        try:
            if pd.isna(value):
                return None
        except TypeError:
            pass

        if isinstance(value, pd.Timestamp):
            return value.to_pydatetime()

        if isinstance(value, datetime):
            return value

        return None
