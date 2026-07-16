from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class LapImportResponse(BaseModel):
    race_session_id: UUID
    source: str
    laps_upserted: int
    laps_skipped: int


class LapResponse(BaseModel):
    driver_number: str
    abbreviation: str | None

    lap_number: int
    lap_start_at: datetime | None

    lap_time_ms: int | None
    sector_1_time_ms: int | None
    sector_2_time_ms: int | None
    sector_3_time_ms: int | None

    speed_i1: Decimal | None
    speed_i2: Decimal | None
    speed_fl: Decimal | None
    speed_st: Decimal | None

    stint: int | None
    compound: str | None
    tyre_life: Decimal | None
    fresh_tyre: bool | None

    position: int | None
    track_status: str | None
    is_personal_best: bool | None
    is_accurate: bool | None
    deleted: bool | None
    deleted_reason: str | None