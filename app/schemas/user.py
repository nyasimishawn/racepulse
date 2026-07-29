from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class CurrentUserProfileUpdate(BaseModel):
    display_name: str | None = Field(
        default=None,
        max_length=120,
    )

    @field_validator("display_name")
    @classmethod
    def validate_display_name(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        display_name = value.strip()

        if not display_name:
            raise ValueError("Display name must not be blank.")

        return display_name


class CurrentUserProfileResponse(BaseModel):
    profile_id: UUID
    username: str | None
    email: str | None
    email_verified: bool
    display_name: str | None
    roles: list[str]
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime