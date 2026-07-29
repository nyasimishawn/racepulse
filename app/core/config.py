from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "RacePulse API"
    environment: str = "development"
    debug: bool = True
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
    keycloak_clock_skew_seconds: int = 30

    @property
    def keycloak_configured(self) -> bool:
        return (
            self.keycloak_enabled
            and bool(self.keycloak_issuer_url)
            and bool(self.keycloak_audience)
        )

    @property
    def keycloak_jwks_url(self) -> str:
        issuer_url = self.keycloak_issuer_url.rstrip("/")
        return (
            f"{issuer_url}/protocol/openid-connect/certs"
        )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()