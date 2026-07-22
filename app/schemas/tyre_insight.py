from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class CompoundBaselineResponse(BaseModel):
    compound: str
    sample_count: int
    fastest_lap_time_ms: int
    robust_fastest_lap_time_ms: float
    baseline_lap_numbers: list[int]
    data_quality_flags: list[str] = Field(default_factory=list)


class CompoundRelativeLapScoreResponse(BaseModel):
    lap_number: int
    compound: str | None
    tyre_life: float | None
    lap_time_ms: int | None
    baseline_ms: float | None
    delta_to_compound_baseline_ms: float | None
    band_ms: int
    classification: str
    included_in_degradation_trend: bool
    data_quality_flags: list[str] = Field(default_factory=list)


class TheoreticalTyreDegradationResponse(BaseModel):
    source_stint_number: int | None
    compound: str | None
    start_lap: int
    end_lap: int
    clean_lap_count: int
    trend_axis: str
    estimated_pace_change_ms_per_axis_unit: float | None
    estimated_pace_change_over_stint_ms: int | None
    classification: str
    sample_confidence: str
    data_quality_flags: list[str] = Field(default_factory=list)


class TyreInsightResponse(BaseModel):
    metric_version: str
    race_session_id: UUID
    driver_number: str
    driver_name: str
    data_source: str
    baseline_definition: list[str]
    compound_baselines: list[CompoundBaselineResponse]
    stints: list[TheoreticalTyreDegradationResponse]
    lap_scores: list[CompoundRelativeLapScoreResponse]
    disclaimer: str


class LiftCoastZoneObservationResponse(BaseModel):
    lap_number: int
    zone_index: int
    brake_distance_m: float
    lift_start_distance_m: float | None
    lift_distance_before_brake_m: float | None
    min_throttle_before_brake: float | None
    speed_at_lift_kph: float | None
    speed_at_brake_kph: float | None
    classification: str
    evidence_tags: list[str] = Field(default_factory=list)


class LiftCoastLapSummaryResponse(BaseModel):
    lap_number: int
    inferred_zone_count: int
    strongest_lift_distance_before_brake_m: float | None
    classification: str
    evidence_tags: list[str] = Field(default_factory=list)


class LiftCoastInsightResponse(BaseModel):
    metric_version: str
    race_session_id: UUID
    driver_number: str
    driver_name: str
    data_source: str
    detection_definition: list[str]
    lap_summaries: list[LiftCoastLapSummaryResponse]
    zone_observations: list[LiftCoastZoneObservationResponse]
    persistence_lap_ranges: list[str]
    data_quality_flags: list[str] = Field(default_factory=list)
    disclaimer: str
