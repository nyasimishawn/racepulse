from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ContentConfidence(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class CuratedAttributionInput(BaseModel):
    source_url: str = Field(min_length=8, max_length=1000)
    publisher: str = Field(min_length=1, max_length=255)
    published_at: datetime
    confidence: ContentConfidence = ContentConfidence.UNVERIFIED
    data_quality_flags: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        normalized = value.strip()
        parsed = urlparse(normalized)

        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Source URL must be an absolute HTTP(S) URL.")

        return normalized

    @field_validator("publisher")
    @classmethod
    def normalize_publisher(cls, value: str) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError("Publisher must not be blank.")

        return normalized

    @field_validator("data_quality_flags")
    @classmethod
    def normalize_data_quality_flags(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().upper() for value in values]

        if any(not value for value in normalized):
            raise ValueError("Data-quality flags must not be blank.")

        if len(normalized) != len(set(normalized)):
            raise ValueError("Data-quality flags must be unique.")

        return normalized


class CuratedAttributionResponse(BaseModel):
    source_url: str
    publisher: str
    published_at: datetime
    confidence: ContentConfidence
    data_quality_flags: list[str]


class DriverProfileUpsertRequest(CuratedAttributionInput):
    biography: str = Field(min_length=1, max_length=20_000)

    @field_validator("biography")
    @classmethod
    def normalize_biography(cls, value: str) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError("Biography must not be blank.")

        return normalized


class TeamProfileUpsertRequest(DriverProfileUpsertRequest):
    pass


class ProfileNotableMomentCreateRequest(CuratedAttributionInput):
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=20_000)
    occurred_at: datetime | None = None

    @field_validator("title", "description")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError("This field must not be blank.")

        return normalized


class ProfileNotableMomentUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = Field(
        default=None,
        min_length=1,
        max_length=20_000,
    )
    occurred_at: datetime | None = None
    source_url: str | None = Field(default=None, max_length=1000)
    publisher: str | None = Field(default=None, max_length=255)
    published_at: datetime | None = None
    confidence: ContentConfidence | None = None
    data_quality_flags: list[str] | None = Field(default=None, max_length=20)

    @field_validator("title", "description", "publisher")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None

        normalized = value.strip()

        if not normalized:
            raise ValueError("This field must not be blank.")

        return normalized

    @field_validator("source_url")
    @classmethod
    def validate_optional_source_url(cls, value: str | None) -> str | None:
        if value is None:
            return None

        return CuratedAttributionInput.validate_source_url(value)

    @field_validator("data_quality_flags")
    @classmethod
    def normalize_optional_data_quality_flags(
        cls,
        values: list[str] | None,
    ) -> list[str] | None:
        if values is None:
            return None

        return CuratedAttributionInput.normalize_data_quality_flags(values)


class NotableMomentResponse(BaseModel):
    id: UUID
    title: str
    description: str
    occurred_at: datetime | None
    attribution: CuratedAttributionResponse
    created_at: datetime
    updated_at: datetime


class DriverSummaryResponse(BaseModel):
    id: UUID
    driver_number: str
    abbreviation: str | None
    first_name: str | None
    last_name: str | None
    full_name: str | None
    country_code: str | None
    source: str


class TeamSummaryResponse(BaseModel):
    id: UUID
    name: str
    colour: str | None
    source: str


class DriverProfileResponse(DriverSummaryResponse):
    profile_id: UUID | None
    biography: str | None
    attribution: CuratedAttributionResponse | None
    notable_moments: list[NotableMomentResponse]


class TeamProfileResponse(TeamSummaryResponse):
    profile_id: UUID | None
    biography: str | None
    attribution: CuratedAttributionResponse | None
    notable_moments: list[NotableMomentResponse]


class TimingCoverageResponse(BaseModel):
    source_names: list[str]
    available_years: list[int]
    earliest_imported_session_at: datetime | None
    latest_imported_session_at: datetime | None
    race_result_count: int
    recorded_lap_count: int
    complete_historical_coverage: bool
    disclaimer: str


class TimingHistoryStatsResponse(BaseModel):
    race_entries: int
    wins: int
    podiums: int
    points: Decimal
    best_finish: int | None
    recorded_laps_led: int


class SeasonTimingHistoryStatsResponse(TimingHistoryStatsResponse):
    year: int


class DriverHistoryResponse(BaseModel):
    driver: DriverSummaryResponse
    filter_year: int | None
    totals: TimingHistoryStatsResponse
    seasons: list[SeasonTimingHistoryStatsResponse]
    coverage: TimingCoverageResponse


class TeamHistoryResponse(BaseModel):
    team: TeamSummaryResponse
    filter_year: int | None
    totals: TimingHistoryStatsResponse
    seasons: list[SeasonTimingHistoryStatsResponse]
    coverage: TimingCoverageResponse
