from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DriverPreview:
    driver_number: str
    abbreviation: str | None
    full_name: str | None
    team_name: str | None
    team_colour: str | None
    country_code: str | None
    classified_position: str | None


@dataclass(frozen=True, slots=True)
class SessionPreview:
    source: str
    year: int
    meeting_name: str
    official_meeting_name: str | None
    session_name: str
    session_identifier: str
    event_date: datetime | None
    country_name: str | None
    location: str | None
    drivers: tuple[DriverPreview, ...]


class TimingDataProvider(ABC):
    @abstractmethod
    def get_session_preview(
        self,
        *,
        year: int,
        event_name: str,
        session_identifier: str,
    ) -> SessionPreview:
        pass