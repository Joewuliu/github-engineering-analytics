from pydantic import BaseModel


class GitHubRepository(BaseModel):
    id: int
    full_name: str
