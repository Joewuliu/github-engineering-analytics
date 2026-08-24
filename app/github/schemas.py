from pydantic import BaseModel


class GitHubRepository(BaseModel):
    id: int
    full_name: str


class GitHubAuthenticatedUser(BaseModel):
    id: int
    login: str
