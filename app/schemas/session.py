from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class SessionSummaryResponse(BaseModel):
    id: UUID
    meeting_id: UUID
    source: str
    year: int

    meeting_name: str
    official_meeting_name: str | None
    country_name: str | None
    location: str | None
    event_date: datetime | None

    session_name: str
    session_identifier: str
    session_type: str
    started_at: datetime | None
    created_at: datetime


class TimingTowerRowResponse(BaseModel):
    position: int | None
    classified_position: str | None
    grid_position: int | None

    q1_time_ms: int | None
    q2_time_ms: int | None
    q3_time_ms: int | None

    driver_number: str
    abbreviation: str | None
    driver_name: str
    country_code: str | None

    team_name: str | None
    team_colour: str | None

    status: str | None
    points: Decimal | None


class SessionDetailResponse(SessionSummaryResponse):
    results: list[TimingTowerRowResponse]