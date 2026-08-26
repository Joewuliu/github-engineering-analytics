from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.github.client import GitHubClient
from app.github.schemas import GitHubPullRequest, GitHubReview
from app.models.pull_request import PullRequest
from app.models.pull_request_review import PullRequestReview
from app.models.repository import Repository
from app.models.user import User
from app.services.repositories import get_tracked_repository

# Milestone 6 deliberately bounds sync to a small, fixed window: each pull
# request requires its own separate GitHub "list reviews" request, so a
# larger limit multiplies outbound GitHub API calls quickly and risks
# exhausting the unauthenticated rate limit (60/hour) on a single sync call.
# Replace this with real incremental/background sync in a later milestone.
MAX_PULL_REQUESTS_PER_SYNC = 25


async def sync_repository(
    repository_id: int, user: User, db: AsyncSession, github_client: GitHubClient
) -> tuple[Repository, int, int]:
    """Fetch this user's tracked repository's recent PRs/reviews and persist them.

    All GitHub calls happen before any database write: the full bounded PR
    list and every PR's reviews are fetched and held in memory first, then
    persistence happens as a single all-or-nothing transaction.
    """
    repository = await get_tracked_repository(repository_id, user, db)

    github_prs = await github_client.list_pull_requests(
        repository.full_name, limit=MAX_PULL_REQUESTS_PER_SYNC
    )
    reviews_by_pr: dict[int, list[GitHubReview]] = {
        github_pr.id: await github_client.list_reviews(repository.full_name, github_pr.number)
        for github_pr in github_prs
    }

    for github_pr in github_prs:
        pull_request_id = await _upsert_pull_request(db, repository.id, github_pr)
        for github_review in reviews_by_pr[github_pr.id]:
            await _upsert_review(db, pull_request_id, github_review)

    await db.commit()

    reviews_processed = sum(len(reviews) for reviews in reviews_by_pr.values())
    return repository, len(github_prs), reviews_processed


async def _upsert_pull_request(
    db: AsyncSession, repository_id: int, github_pr: GitHubPullRequest
) -> int:
    insert_stmt = pg_insert(PullRequest).values(
        github_id=github_pr.id,
        repository_id=repository_id,
        number=github_pr.number,
        author_login=github_pr.author_login,
        title=github_pr.title,
        state=github_pr.state,
        github_created_at=github_pr.created_at,
        github_updated_at=github_pr.updated_at,
        closed_at=github_pr.closed_at,
        merged_at=github_pr.merged_at,
    )
    # Conflict target is github_id only -- the identity constraint. A
    # collision on the separate UNIQUE(repository_id, number) guard instead
    # is not caught here and raises IntegrityError normally: that would mean
    # an inconsistent github_id for an existing (repo, number) pair, which is
    # an unexpected data-integrity failure, not a benign re-sync.
    upsert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=[PullRequest.github_id],
        set_={
            # Mutable fields only. github_id, repository_id, number, and
            # github_created_at are identity/history and are never rewritten.
            "author_login": insert_stmt.excluded.author_login,
            "title": insert_stmt.excluded.title,
            "state": insert_stmt.excluded.state,
            "github_updated_at": insert_stmt.excluded.github_updated_at,
            "closed_at": insert_stmt.excluded.closed_at,
            "merged_at": insert_stmt.excluded.merged_at,
            # ORM `onupdate` callables do not fire for Core upserts -- set
            # updated_at explicitly.
            "updated_at": func.now(),
        },
    ).returning(PullRequest.id)

    result = await db.execute(upsert_stmt)
    pull_request_id: int = result.scalar_one()
    return pull_request_id


async def _upsert_review(
    db: AsyncSession, pull_request_id: int, github_review: GitHubReview
) -> None:
    insert_stmt = pg_insert(PullRequestReview).values(
        github_id=github_review.id,
        pull_request_id=pull_request_id,
        reviewer_login=github_review.reviewer_login,
        state=github_review.state,
        submitted_at=github_review.submitted_at,
    )
    upsert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=[PullRequestReview.github_id],
        set_={
            "reviewer_login": insert_stmt.excluded.reviewer_login,
            "state": insert_stmt.excluded.state,
            "submitted_at": insert_stmt.excluded.submitted_at,
            "updated_at": func.now(),
        },
    )
    await db.execute(upsert_stmt)
