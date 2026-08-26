from dataclasses import dataclass
from datetime import datetime
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pull_request import PullRequest
from app.models.pull_request_review import PullRequestReview
from app.models.repository import Repository
from app.models.user import User
from app.services.repositories import get_tracked_repository


@dataclass
class RepositoryMetricsResult:
    """Full-precision metrics for one repository. Rounding is a route concern."""

    repository: Repository
    total_pull_requests: int
    merged_pull_requests: int
    merge_rate: float | None
    median_pr_cycle_time_hours: float | None
    median_time_to_first_review_hours: float | None


async def get_repository_metrics(
    repository_id: int, user: User, db: AsyncSession
) -> RepositoryMetricsResult:
    """Compute engineering metrics for a tracked repository from stored data only.

    Never calls GitHub -- reads exclusively from the locally ingested
    PullRequest/PullRequestReview rows (see Milestone 6's bounded sync).
    """
    repository = await get_tracked_repository(repository_id, user, db)

    total = await db.scalar(
        select(func.count(PullRequest.id)).where(PullRequest.repository_id == repository_id)
    )
    total = total or 0

    merged = await db.scalar(
        select(func.count(PullRequest.id)).where(
            PullRequest.repository_id == repository_id,
            PullRequest.merged_at.is_not(None),
        )
    )
    merged = merged or 0

    merge_rate = (merged / total) if total > 0 else None

    return RepositoryMetricsResult(
        repository=repository,
        total_pull_requests=total,
        merged_pull_requests=merged,
        merge_rate=merge_rate,
        median_pr_cycle_time_hours=await _median_pr_cycle_time_hours(db, repository_id),
        median_time_to_first_review_hours=await _median_time_to_first_review_hours(
            db, repository_id
        ),
    )


async def _median_pr_cycle_time_hours(db: AsyncSession, repository_id: int) -> float | None:
    result = await db.execute(
        select(PullRequest.github_created_at, PullRequest.merged_at).where(
            PullRequest.repository_id == repository_id,
            PullRequest.merged_at.is_not(None),
        )
    )

    durations_hours = [
        (merged_at - created_at).total_seconds() / 3600
        for created_at, merged_at in result.all()
        # Defensive: a merge timestamp before creation is malformed/historical
        # data, not a real negative engineering duration -- excluded.
        if merged_at >= created_at
    ]
    if not durations_hours:
        return None
    result_hours: float = median(durations_hours)
    return result_hours


async def _median_time_to_first_review_hours(db: AsyncSession, repository_id: int) -> float | None:
    # One join covering every qualifying review for every PR in the
    # repository at once -- no per-PR query (no N+1). Independent of merge
    # status: an open, never-merged PR with a review still contributes.
    result = await db.execute(
        select(PullRequest.id, PullRequest.github_created_at, PullRequestReview.submitted_at)
        .join(PullRequestReview, PullRequestReview.pull_request_id == PullRequest.id)
        .where(
            PullRequest.repository_id == repository_id,
            PullRequestReview.submitted_at.is_not(None),
        )
    )

    created_at_by_pr: dict[int, datetime] = {}
    earliest_valid_submission_by_pr: dict[int, datetime] = {}
    for pr_id, created_at, submitted_at in result.all():
        if submitted_at < created_at:
            # Defensive: a review submitted before the PR was created is a
            # malformed/historical anomaly, not a valid "first response".
            continue
        created_at_by_pr[pr_id] = created_at
        earliest = earliest_valid_submission_by_pr.get(pr_id)
        if earliest is None or submitted_at < earliest:
            earliest_valid_submission_by_pr[pr_id] = submitted_at

    durations_hours = [
        (earliest_valid_submission_by_pr[pr_id] - created_at_by_pr[pr_id]).total_seconds() / 3600
        for pr_id in earliest_valid_submission_by_pr
    ]
    if not durations_hours:
        return None
    result_hours: float = median(durations_hours)
    return result_hours
