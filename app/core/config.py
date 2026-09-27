from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "RacePulse API"
    environment: str = "development"
    # Production must opt in to debugging explicitly; local .env may set it.
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    cors_allowed_origin_regex: str = (
        r"^http://(localhost|127\.0\.0\.1):\d+$"
    )

    database_url: str = (
        "postgresql+psycopg://racepulse:racepulse@localhost:5432/racepulse"
    )
    redis_url: str = "redis://localhost:6379/0"
    fastf1_cache_path: str = "data/fastf1-cache"
    fastf1_verify_ssl: bool = True

    keycloak_enabled: bool = False
    keycloak_issuer_url: str = ""
    keycloak_audience: str = ""
    keycloak_swagger_client_id: str = ""
    keycloak_clock_skew_seconds: int = 30
    keycloak_groups_claim: str = "groups"

    # These values are server-only. Never put the admin client secret in
    # Flutter or any other public client.
    keycloak_admin_realm_url: str = ""
    keycloak_admin_client_id: str = ""
    keycloak_admin_client_secret: str = ""
    keycloak_admin_timeout_seconds: float = 10.0

    keycloak_public_client_id: str = ""
    keycloak_public_redirect_uri: str = ""
    keycloak_public_registration_enabled: bool = False
    keycloak_send_verification_email: bool = False
    keycloak_default_role: str = "fan"
    keycloak_assignable_roles: str = "fan,editor,admin"
    fantasy_guest_mode: bool = True

    # Request handling and abuse protection. These are intentionally modest
    # defaults for local development; production operators should set them
    # explicitly for their traffic profile.
    log_level: str = "INFO"
    trust_proxy_headers: bool = False
    registration_rate_limit: int = 5
    registration_rate_window_seconds: int = 3_600
    expensive_request_rate_limit: int = 30
    expensive_request_rate_window_seconds: int = 3_600

    # Redis Streams is the single transport for durable background jobs.
    # PostgreSQL remains the source of truth for job state and recovery.
    job_stream_key: str = "racepulse:jobs:v1"
    job_consumer_group: str = "racepulse-workers"
    job_max_attempts: int = 3
    job_lease_seconds: int = 300
    job_recovery_interval_seconds: int = 30

    @property
    def keycloak_configured(self) -> bool:
        return (
            self.keycloak_enabled
            and bool(self.keycloak_issuer_url)
            and bool(self.keycloak_audience)
        )

    @property
    def fantasy_guest_access_enabled(self) -> bool:
        return self.environment == "development" and self.fantasy_guest_mode

    @property
    def keycloak_swagger_configured(self) -> bool:
        return (
            self.keycloak_configured
            and bool(self.keycloak_swagger_client_id)
        )

    @property
    def keycloak_admin_configured(self) -> bool:
        return (
            self.keycloak_configured
            and bool(self.keycloak_admin_realm_url)
            and bool(self.keycloak_admin_client_id)
            and bool(self.keycloak_admin_client_secret)
        )

    @property
    def keycloak_assignable_role_names(self) -> frozenset[str]:
        return frozenset(
            role.strip().casefold()
            for role in self.keycloak_assignable_roles.split(",")
            if role.strip()
        )

    @property
    def keycloak_authorization_url(self) -> str:
        issuer_url = self.keycloak_issuer_url.rstrip("/")
        return f"{issuer_url}/protocol/openid-connect/auth"

    @property
    def keycloak_token_url(self) -> str:
        issuer_url = self.keycloak_issuer_url.rstrip("/")
        return f"{issuer_url}/protocol/openid-connect/token"

    @property
    def keycloak_jwks_url(self) -> str:
        issuer_url = self.keycloak_issuer_url.rstrip("/")
        return f"{issuer_url}/protocol/openid-connect/certs"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
