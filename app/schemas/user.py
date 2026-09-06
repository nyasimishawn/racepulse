from datetime import datetime
from uuid import UUID

from pydantic import (
    BaseModel,
    EmailStr,
    Field,
    SecretStr,
    field_validator,
)


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
    groups: list[str] = Field(default_factory=list)
    given_name: str | None = None
    family_name: str | None = None
    is_active: bool
    last_seen_at: datetime
    identity_synced_at: datetime
    created_at: datetime
    updated_at: datetime


class AccountRegistrationRequest(BaseModel):
    username: str = Field(
        min_length=3,
        max_length=30,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,29}$",
    )
    email: EmailStr
    display_name: str = Field(min_length=2, max_length=120)
    password: SecretStr

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip()

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        display_name = value.strip()
        if not display_name:
            raise ValueError("Display name must not be blank.")
        return display_name

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: SecretStr) -> SecretStr:
        password = value.get_secret_value()
        if len(password) < 12:
            raise ValueError("Password must contain at least 12 characters.")
        if password.isspace():
            raise ValueError("Password must not contain only whitespace.")
        return value


class AccountRegistrationResponse(BaseModel):
    profile: CurrentUserProfileResponse
    verification_email_sent: bool
    sign_in_required: bool = True


class AdminUserProfileResponse(BaseModel):
    profile_id: UUID
    keycloak_subject: str
    username: str | None
    email: str | None
    email_verified: bool
    display_name: str | None
    keycloak_roles: list[str]
    keycloak_client_roles: list[str] = Field(default_factory=list)
    keycloak_groups: list[str] = Field(default_factory=list)
    given_name: str | None = None
    family_name: str | None = None
    is_active: bool
    last_seen_at: datetime
    identity_synced_at: datetime
    created_at: datetime
    updated_at: datetime


class AdminUserListResponse(BaseModel):
    items: list[AdminUserProfileResponse]
    page: int
    page_size: int
    total: int


class AdminUserBulkSynchronizeResponse(BaseModel):
    items: list[AdminUserProfileResponse]
    synchronized_count: int

class AdminUserRoleUpdate(BaseModel):
    roles: list[str] = Field(min_length=1, max_length=10)

    @field_validator("roles")
    @classmethod
    def normalize_roles(cls, values: list[str]) -> list[str]:
        roles = sorted(
            {
                value.strip().casefold()
                for value in values
                if value.strip()
            }
        )
        if not roles:
            raise ValueError("At least one role is required.")
        if "fan" not in roles:
            raise ValueError("Every RacePulse account must keep the fan role.")
        return roles


class AdminUserStatusUpdate(BaseModel):
    enabled: bool


class AccountActionResponse(BaseModel):
    message: str
