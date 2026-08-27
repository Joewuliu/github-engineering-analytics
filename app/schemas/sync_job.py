from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class SyncJobCreatedResponse(BaseModel):
    job_id: UUID
    repository_id: int
    status: str


class SyncJobResponse(BaseModel):
    job_id: UUID
    repository_id: int
    status: str
    pull_requests_processed: int | None
    reviews_processed: int | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    safe_error_code: str | None
    safe_error_message: str | None
