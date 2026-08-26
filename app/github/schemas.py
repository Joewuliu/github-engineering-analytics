from datetime import datetime

from pydantic import BaseModel


class GitHubRepository(BaseModel):
    id: int
    full_name: str


class GitHubAuthenticatedUser(BaseModel):
    id: int
    login: str


class GitHubActor(BaseModel):
    """A GitHub user reference nested in another payload (e.g. PR/review author)."""

    login: str


class GitHubPullRequest(BaseModel):
    id: int
    number: int
    title: str
    state: str
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None
    merged_at: datetime | None = None
    # GitHub returns `user: null` for PRs authored by since-deleted accounts.
    user: GitHubActor | None = None

    @property
    def author_login(self) -> str | None:
        return self.user.login if self.user else None


class GitHubReview(BaseModel):
    id: int
    state: str
    submitted_at: datetime | None = None
    # PENDING reviews (and reviews by since-deleted accounts) may lack a user.
    user: GitHubActor | None = None

    @property
    def reviewer_login(self) -> str | None:
        return self.user.login if self.user else None
