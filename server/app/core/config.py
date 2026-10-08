"""Environment-driven application configuration."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings consumed by Foundation.

    DATABASE_URL is the sole database endpoint contract. In production it is
    expected to point at the Cloudflare Hyperdrive endpoint; local development
    may point directly at Neon or a local PostgreSQL instance.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field(validation_alias="APP_NAME")
    environment: str = Field(validation_alias="ENVIRONMENT")
    log_level: str = Field(validation_alias="LOG_LEVEL")
    database_url: SecretStr = Field(validation_alias="DATABASE_URL")
    database_connect_timeout_seconds: int = Field(
        validation_alias="DATABASE_CONNECT_TIMEOUT_SECONDS", ge=1, le=120
    )
    database_pool_size: int = Field(validation_alias="DATABASE_POOL_SIZE", ge=1, le=50)
    database_max_overflow: int = Field(validation_alias="DATABASE_MAX_OVERFLOW", ge=0, le=100)
    jwt_secret_key: SecretStr = Field(validation_alias="JWT_SECRET_KEY")
    jwt_algorithm: str = Field(validation_alias="JWT_ALGORITHM")
    jwt_access_token_minutes: int = Field(
        validation_alias="JWT_ACCESS_TOKEN_MINUTES", ge=1, le=1440
    )
    google_oauth_client_id: str | None = Field(
        default=None, validation_alias="GOOGLE_OAUTH_CLIENT_ID"
    )
    google_oauth_client_secret: SecretStr | None = Field(
        default=None, validation_alias="GOOGLE_OAUTH_CLIENT_SECRET"
    )
    google_oauth_redirect_uri: str = Field(
        default="http://localhost:8000/auth/google/callback",
        validation_alias="GOOGLE_OAUTH_REDIRECT_URI",
    )

    @property
    def database_dsn(self) -> str:
        """Return the connection string only to database infrastructure code."""
        return self.database_url.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    """Load and validate settings once per process; fail clearly if required values are absent."""
    return Settings()
