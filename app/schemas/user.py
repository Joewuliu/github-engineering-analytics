from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    github_id: int
    github_login: str
    created_at: datetime
    updated_at: datetime
