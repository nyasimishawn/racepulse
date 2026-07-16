from datetime import datetime

from pydantic import BaseModel


class DriverPreviewResponse(BaseModel):
    driver_number: str
    abbreviation: str | None
    full_name: str | None
    team_name: str | None
    team_colour: str | None
    country_code: str | None
    classified_position: str | None


class FastF1SessionPreviewResponse(BaseModel):
    source: str
    year: int
    meeting_name: str
    official_meeting_name: str | None
    session_name: str
    session_identifier: str
    event_date: datetime | None
    country_name: str | None
    location: str | None
    drivers: list[DriverPreviewResponse]