from datetime import datetime
from pathlib import Path

import fastf1
import pandas as pd
import logging

import urllib3
from urllib3.exceptions import InsecureRequestWarning

logger = logging.getLogger(__name__)

from app.core.config import settings
from app.providers.base_provider import (
    DriverPreview,
    ProviderError,
    SessionPreview,
    TimingDataProvider,
)


class FastF1Provider(TimingDataProvider):
    source_name = "FASTF1"

    def __init__(self) -> None:
        cache_path = Path(settings.fastf1_cache_path)
        cache_path.mkdir(parents=True, exist_ok=True)

        fastf1.Cache.enable_cache(str(cache_path))
        self._configure_ssl_verification()

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