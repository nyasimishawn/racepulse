from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.session_telemetry_import import (
    SessionTelemetryDriverStatus,
    SessionTelemetryImportStatus,
)


class SessionTelemetryImportCreate(BaseModel):
    driver_numbers: list[str] | None = Field(
        default=None,
        description=(
            "Driver numbers to import. Leave null to target every driver "
            "with imported laps in the session."
        ),
    )
    max_laps_per_driver: int | None = Field(
        default=6, ge=1, le=25,
        description="Null imports all laps; full weekends use null.",
    )
    clean_laps_only: bool = True

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


class SessionTelemetryImportDriverResponse(BaseModel):
    driver_number: str
    abbreviation: str | None
    full_name: str | None
    status: SessionTelemetryDriverStatus

    selected_lap_numbers: list[int]
    unavailable_lap_numbers: list[int]
    failed_lap_numbers: list[int]

    laps_requested: int
    laps_processed: int
    laps_with_telemetry: int
    laps_unavailable: int
    laps_failed: int
    points_written: int
    coverage_percent: float | None

    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None


class SessionTelemetryImportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    race_session_id: UUID
    source: str
    status: SessionTelemetryImportStatus

    requested_driver_numbers: list[str] | None
    max_laps_per_driver: int | None
    clean_laps_only: bool
    driver_results: list[SessionTelemetryImportDriverResponse]

    progress_percentage: int

    drivers_total: int
    drivers_processed: int
    drivers_completed: int
    drivers_partial: int
    drivers_skipped: int
    drivers_failed: int

    laps_requested: int
    laps_processed: int
    laps_with_telemetry: int
    laps_unavailable: int
    laps_failed: int
    points_written: int

    current_driver_number: str | None
    error_message: str | None
    durable_job_id: UUID | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SessionTelemetryCoverageDriverResponse(BaseModel):
    driver_number: str
    abbreviation: str | None
    full_name: str | None

    imported_lap_count: int
    telemetry_lap_count: int
    telemetry_point_count: int
    coverage_percent: Decimal


class SessionTelemetryCoverageResponse(BaseModel):
    race_session_id: UUID

    latest_telemetry_import_id: UUID | None
    latest_telemetry_import_status: (
        SessionTelemetryImportStatus | None
    )

    eligible_driver_count: int
    drivers_with_telemetry: int

    imported_lap_count: int
    telemetry_lap_count: int
    telemetry_point_count: int
    coverage_percent: Decimal

    any_telemetry_available: bool
    drivers: list[SessionTelemetryCoverageDriverResponse]
