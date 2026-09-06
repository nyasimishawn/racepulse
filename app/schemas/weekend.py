from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.durable_job import DurableJobResponse


class WeekendSelectionRequest(BaseModel):
    year: int = Field(ge=2018, le=2100)
    event_name: str = Field(min_length=1, max_length=150)

    @field_validator("event_name")
    @classmethod
    def normalize_event_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("Select a race weekend.")
        return value


class WeekendSessionSchedule(BaseModel):
    identifier: str
    name: str
    scheduled_at: datetime | None = None


class WeekendSchedule(BaseModel):
    year: int
    round_number: int
    event_name: str
    aliases: list[str]
    sessions: list[WeekendSessionSchedule]


class DownloadStage(BaseModel):
    status: Literal[
        "PENDING",
        "RUNNING",
        "COMPLETED",
        "PARTIAL",
        "UNAVAILABLE",
        "FAILED",
    ] = "PENDING"
    details: dict = Field(default_factory=dict)


class WeekendSessionDownload(WeekendSessionSchedule):
    race_session_id: UUID | None = None
    import_job_id: UUID | None = None
    telemetry_import_id: UUID | None = None
    map_import_id: UUID | None = None
    stages: dict[str, DownloadStage] = Field(default_factory=dict)


class WeekendDownloadResponse(BaseModel):
    id: UUID
    year: int
    round_number: int
    event_name: str
    meeting_id: UUID | None
    status: str
    progress_percentage: int
    cached: bool
    sessions: list[WeekendSessionDownload]
    job: DurableJobResponse | None
