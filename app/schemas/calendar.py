from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, HttpUrl, model_validator

ScheduleStatus = Literal[
    "SCHEDULED", "POSTPONED", "CANCELLED", "IN_PROGRESS", "COMPLETED"
]


class CalendarSessionInput(BaseModel):
    identifier: Literal["FP1", "FP2", "FP3", "SQ", "S", "Q", "R"]
    name: str = Field(min_length=1, max_length=100)
    starts_at: AwareDatetime | None = None
    status: ScheduleStatus = "SCHEDULED"

    @model_validator(mode="after")
    def validate_time(self):
        if self.status in {"SCHEDULED", "IN_PROGRESS", "COMPLETED"}:
            if self.starts_at is None:
                raise ValueError("An active session needs a start time.")
        if self.starts_at is not None:
            self.starts_at = self.starts_at.astimezone(UTC)
        return self


class CalendarWeekendInput(BaseModel):
    year: int = Field(ge=2018, le=2100)
    round_number: int = Field(ge=1, le=40)
    event_name: str = Field(min_length=1, max_length=150)
    status: ScheduleStatus = "SCHEDULED"
    meeting_id: UUID | None = None
    source_url: HttpUrl
    source_checked_at: AwareDatetime
    change_reason: str = Field(min_length=1, max_length=1000)
    sessions: list[CalendarSessionInput] = Field(min_length=1, max_length=7)

    @model_validator(mode="after")
    def validate_sessions(self):
        identifiers = [session.identifier for session in self.sessions]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Session identifiers must be unique.")
        if "R" not in identifiers:
            raise ValueError("Include the race session (R).")
        self.source_checked_at = self.source_checked_at.astimezone(UTC)
        if self.source_checked_at > datetime.now(UTC):
            raise ValueError("Source verification cannot be in the future.")
        return self
