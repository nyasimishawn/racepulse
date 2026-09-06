from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.session_map_import import (
    SessionMapDriverStatus,
    SessionMapImportStatus,
)


class SessionMapImportCreate(BaseModel):
    driver_numbers: list[str] | None = Field(
        default=None,
        description=(
            "Driver numbers to import. Leave null to import all "
            "drivers in the session."
        ),
    )

    sample_interval_ms: Literal[0, 250, 500, 1000] = Field(
        default=250,
        description="0 preserves every usable provider position sample.",
    )

    @field_validator("driver_numbers")
    @classmethod
    def normalize_driver_numbers(
        cls,
        value: list[str] | None,
    ) -> list[str] | None:
        if value is None:
            return None

        numbers = {
            number.strip()
            for number in value
            if number and number.strip()
        }

        if not numbers:
            raise ValueError(
                "driver_numbers must contain at least one driver."
            )

        return sorted(
            numbers,
            key=lambda number: (
                0,
                int(number),
            )
            if number.isdigit()
            else (1, number),
        )


class SessionMapImportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    race_session_id: UUID
    source: str
    status: SessionMapImportStatus

    sample_interval_ms: int
    requested_driver_numbers: list[str] | None

    progress_percentage: int

    drivers_total: int
    drivers_processed: int
    drivers_completed: int
    drivers_partial: int
    drivers_skipped: int
    drivers_failed: int

    samples_written: int
    current_driver_number: str | None
    error_message: str | None
    durable_job_id: UUID | None

    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SessionMapImportDriverResponse(BaseModel):
    driver_number: str
    abbreviation: str | None
    full_name: str | None

    status: SessionMapDriverStatus

    raw_samples_seen: int
    valid_samples: int
    samples_written: int

    first_sample_session_time_ms: int | None
    last_sample_session_time_ms: int | None
    largest_gap_ms: int | None
    coverage_percent: Decimal | None

    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None


class SessionMapCoverageResponse(BaseModel):
    race_session_id: UUID

    active_map_import_id: UUID | None
    active_map_import_status: SessionMapImportStatus | None

    sample_interval_ms: int | None

    eligible_driver_count: int
    ready_driver_count: int

    dataset_ready: bool
    full_session_track_map_ready: bool

    drivers: list[SessionMapImportDriverResponse]
