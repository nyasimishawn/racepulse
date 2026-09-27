from __future__ import annotations

from datetime import datetime
from enum import Enum
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.profiles import ContentConfidence
from app.schemas.paddock import PaddockContext


class EditorialUpdateType(str, Enum):
    UPGRADE = "UPGRADE"
    PENALTY = "PENALTY"
    GRID_DROP = "GRID_DROP"
    RACE_CONTROL = "RACE_CONTROL"
    FIA_UPDATE = "FIA_UPDATE"
    TEAM_UPDATE = "TEAM_UPDATE"
    PIRELLI_UPDATE = "PIRELLI_UPDATE"


class EditorialPublicationStatus(str, Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"


class EditorialUpdateCreateRequest(BaseModel):
    context: PaddockContext = Field(default_factory=PaddockContext)
    update_type: EditorialUpdateType
    publication_status: EditorialPublicationStatus = (
        EditorialPublicationStatus.DRAFT
    )
    title: str = Field(min_length=1, max_length=240)
    body: str = Field(min_length=1, max_length=20_000)

    meeting_id: UUID | None = None
    race_session_id: UUID | None = None
    driver_id: UUID | None = None
    team_id: UUID | None = None

    source_url: str = Field(min_length=8, max_length=1000)
    publisher: str = Field(min_length=1, max_length=255)
    published_at: datetime
    confidence: ContentConfidence = ContentConfidence.UNVERIFIED
    data_quality_flags: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("title", "body", "publisher")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError("This field must not be blank.")

        return normalized

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        normalized = value.strip()
        parsed = urlparse(normalized)

        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Source URL must be an absolute HTTP(S) URL.")

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


class EditorialUpdateUpdateRequest(BaseModel):
    context: PaddockContext | None = None
    update_type: EditorialUpdateType | None = None
    publication_status: EditorialPublicationStatus | None = None
    title: str | None = Field(default=None, min_length=1, max_length=240)
    body: str | None = Field(default=None, min_length=1, max_length=20_000)

    meeting_id: UUID | None = None
    race_session_id: UUID | None = None
    driver_id: UUID | None = None
    team_id: UUID | None = None

    source_url: str | None = Field(default=None, max_length=1000)
    publisher: str | None = Field(default=None, max_length=255)
    published_at: datetime | None = None
    confidence: ContentConfidence | None = None
    data_quality_flags: list[str] | None = Field(default=None, max_length=20)

    @field_validator("title", "body", "publisher")
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

        return EditorialUpdateCreateRequest.validate_source_url(value)

    @field_validator("data_quality_flags")
    @classmethod
    def normalize_optional_data_quality_flags(
        cls,
        values: list[str] | None,
    ) -> list[str] | None:
        if values is None:
            return None

        return EditorialUpdateCreateRequest.normalize_data_quality_flags(
            values
        )


class EditorialUpdateResponse(BaseModel):
    context: PaddockContext = Field(default_factory=PaddockContext)
    id: UUID
    update_type: EditorialUpdateType
    publication_status: EditorialPublicationStatus
    title: str
    body: str

    meeting_id: UUID | None
    race_session_id: UUID | None
    driver_id: UUID | None
    team_id: UUID | None

    source_url: str
    publisher: str
    published_at: datetime
    confidence: ContentConfidence
    data_quality_flags: list[str]

    created_at: datetime
    updated_at: datetime
