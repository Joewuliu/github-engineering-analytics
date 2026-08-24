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
    github_api_base_url: str = "https://api.github.com"
    github_token: str | None = None
    github_request_timeout: float = Field(default=10.0, gt=0)

    @field_validator("github_token")
    @classmethod
    def _blank_token_as_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


@lru_cache
def get_settings() -> Settings:
    return Settings()
