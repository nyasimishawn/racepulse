from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class QualifyingReferenceLapResponse(BaseModel):
    id: UUID
    lap_number: int
    lap_time_ms: int

    sector_1_time_ms: int | None
    sector_2_time_ms: int | None
    sector_3_time_ms: int | None

    speed_i1: Decimal | None
    speed_i2: Decimal | None
    speed_fl: Decimal | None
    speed_st: Decimal | None

    compound: str | None
    tyre_life: Decimal | None
    track_status: str | None

    reference_quality: str


class QualifyingSummaryRowResponse(BaseModel):
    driver_id: UUID
    driver_number: str
    abbreviation: str | None
    driver_name: str
    country_code: str | None

    team_name: str | None
    team_colour: str | None

    position: int | None
    classified_position: str | None

    q1_time_ms: int | None
    q2_time_ms: int | None
    q3_time_ms: int | None

    best_lap: QualifyingReferenceLapResponse | None


class QualifyingSummaryResponse(BaseModel):
    qualifying_session_id: UUID
    meeting_id: UUID
    meeting_name: str
    session_name: str

    rows: list[QualifyingSummaryRowResponse]


class RaceQualifyingReferenceResponse(BaseModel):
    race_session_id: UUID
    qualifying_session_id: UUID | None
    driver_number: str

    selection: str

    q1_time_ms: int | None
    q2_time_ms: int | None
    q3_time_ms: int | None

    reference_lap: QualifyingReferenceLapResponse | None
    unavailable_reason: str | None