from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class TelemetryImportResponse(BaseModel):
    race_session_id: UUID
    driver_number: str
    lap_number: int
    telemetry_points_upserted: int


class TelemetryPointResponse(BaseModel):
    relative_time_ms: int
    session_time_ms: int | None
    sampled_at: datetime | None

    speed_kph: Decimal | None
    rpm: int | None
    gear: int | None
    throttle_percentage: Decimal | None
    brake_applied: bool | None
    drs: int | None

    x: Decimal | None
    y: Decimal | None
    z: Decimal | None
    distance_m: Decimal | None

    sample_source: str | None
    is_interpolated: bool