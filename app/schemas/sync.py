from pydantic import BaseModel


class RepositorySyncResponse(BaseModel):
    repository_id: int
    full_name: str
    pull_requests_processed: int
    reviews_processed: int
