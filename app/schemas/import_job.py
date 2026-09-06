from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.import_job import DataSource, ImportJobStatus


class ImportJobCreate(BaseModel):
    source: DataSource = DataSource.FASTF1
    year: int = Field(ge=2018, le=2100)
    event_name: str = Field(min_length=2, max_length=150)
    session_type: str = Field(
        min_length=2,
        max_length=50,
        examples=["Race"],
    )
    imported_session_id: UUID | None


class ImportJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source: DataSource
    year: int
    event_name: str
    session_type: str
    status: ImportJobStatus
    progress_percentage: int
    error_message: str | None
    durable_job_id: UUID | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
