from uuid import UUID

from pydantic import BaseModel


class AttackIndexComponentsResponse(BaseModel):
    qualifying_full_throttle_share: float
    race_full_throttle_share: float
    full_throttle_share_delta: float

    qualifying_brake_zone_share: float
    race_brake_zone_share: float
    brake_zone_share_delta: float

    qualifying_coast_candidate_distance_m: float
    race_coast_candidate_distance_m: float
    extra_coast_candidate_distance_m: float

    qualifying_sample_count: int
    race_sample_count: int
    common_bin_count: int


class AttackIndexResponse(BaseModel):
    metric_version: str

    eligible: bool
    attack_score_10: float | None
    classification: str | None
    confidence: str | None

    race_session_id: UUID
    qualifying_session_id: UUID | None

    driver_number: str

    race_lap_id: UUID
    race_lap_number: int
    qualifying_lap_id: UUID | None
    qualifying_lap_number: int | None

    race_lap_time_ms: int | None
    qualifying_lap_time_ms: int | None
    lap_time_delta_to_qualifying_ms: int | None

    qualifying_reference_quality: str | None
    components: AttackIndexComponentsResponse | None

    ineligibility_reasons: list[str]
    disclaimer: str