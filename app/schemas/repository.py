from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


class RepositoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    github_id: int
    full_name: str
    created_at: datetime
    updated_at: datetime


class RepositoryCreateRequest(BaseModel):
    full_name: str

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, value: str) -> str:
        value = value.strip()
        parts = value.split("/")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError("full_name must be in the form 'owner/repository'")
        return value


class TrackedRepositoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    github_id: int
    full_name: str
    created_at: datetime
    updated_at: datetime
    tracked_at: datetime
