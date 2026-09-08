from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class DashboardTrack(BaseModel):
    meeting_id: UUID
    name: str
    country: str | None
    source: str
    available_years: list[int]
    matching_basis: str


class TrackDriverStats(BaseModel):
    driver_id: UUID
    driver_number: str
    name: str
    race_entries: int = 0
    wins: int = 0
    wins_from_pole: int = 0
    wins_outside_pole: int = 0
    wins_with_unknown_grid: int = 0
    podiums: int = 0
    points: float = 0
    points_missing_count: int = 0
    qualifying_entries: int = 0
    qualifying_poles: int = 0
    average_qualifying_position: float | None = None
    teammate_qualifying_advantage: float | None = None
    teammate_comparison_count: int = 0
    season_points: float = 0
    season_race_entries: int = 0
    season_points_missing_count: int = 0


class TeamWinEstimate(BaseModel):
    team_id: UUID
    name: str
    colour: str | None
    estimated_win_percent: float | None
    season_points: float
    season_wins: int
    track_wins: int
    driver_track_poles: int
    season_form_share: float
    track_history_share: float
    driver_affinity_share: float


class TrackDashboardResponse(BaseModel):
    track: DashboardTrack
    season_year: int
    as_of: datetime
    drivers: list[TrackDriverStats]
    team_estimates: list[TeamWinEstimate]
    track_race_count: int
    track_qualifying_count: int
    season_race_count: int
    latest_season_race_at: datetime | None
    forecast_available: bool
    model_version: str = "track-form-heuristic-v1"
    model_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "season_form": 0.60,
            "track_history": 0.25,
            "driver_affinity": 0.15,
        }
    )
    model_explanation: str
    insights: list[str]
    data_quality_flags: list[str]
    coverage_disclaimer: str
