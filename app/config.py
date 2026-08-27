from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "GitHub Engineering Analytics"
    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/github_analytics"
    db_echo: bool = False
    redis_url: str = "redis://localhost:6379/0"
    github_api_base_url: str = "https://api.github.com"
    github_token: str | None = None
    github_request_timeout: float = Field(default=10.0, gt=0)

    github_oauth_client_id: str | None = None
    github_oauth_client_secret: str | None = None
    github_oauth_callback_url: str = "http://127.0.0.1:8000/auth/github/callback"
    session_max_age_seconds: int = Field(default=60 * 60 * 24 * 14, gt=0)
    oauth_state_max_age_seconds: int = Field(default=600, gt=0)

    @field_validator("github_token", "github_oauth_client_id", "github_oauth_client_secret")
    @classmethod
    def _blank_as_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


@lru_cache
def get_settings() -> Settings:
    return Settings()
