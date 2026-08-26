from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pull_request import PullRequest
from app.models.pull_request_review import PullRequestReview
from app.models.repository import Repository
from app.models.user import User
from app.models.user_repository import UserRepository
from app.services.repository_metrics import get_repository_metrics

BASE = datetime(2024, 1, 1, tzinfo=UTC)


async def _make_user(db_session: AsyncSession, github_id: int, login: str) -> User:
    user = User(github_id=github_id, github_login=login)
    db_session.add(user)
    await db_session.flush()
    return user


async def _make_tracked_repository(
    db_session: AsyncSession, user: User, github_id: int, full_name: str
) -> Repository:
    repository = Repository(github_id=github_id, full_name=full_name)
    db_session.add(repository)
    await db_session.flush()
    db_session.add(UserRepository(user_id=user.id, repository_id=repository.id))
    await db_session.commit()
    return repository


async def _add_pr(
    db_session: AsyncSession,
    repository_id: int,
    github_id: int,
    number: int,
    created_at: datetime = BASE,
    merged_at: datetime | None = None,
    author_login: str | None = "octocat",
) -> PullRequest:
    pull_request = PullRequest(
        github_id=github_id,
        repository_id=repository_id,
        number=number,
        author_login=author_login,
        title=f"PR {number}",
        state="closed" if merged_at else "open",
        github_created_at=created_at,
        github_updated_at=created_at,
        merged_at=merged_at,
    )
    db_session.add(pull_request)
    await db_session.flush()
    return pull_request


async def _add_review(
    db_session: AsyncSession,
    pull_request_id: int,
    github_id: int,
    state: str = "APPROVED",
    submitted_at: datetime | None = None,
    reviewer_login: str | None = "reviewer",
) -> PullRequestReview:
    review = PullRequestReview(
        github_id=github_id,
        pull_request_id=pull_request_id,
        reviewer_login=reviewer_login,
        state=state,
        submitted_at=submitted_at,
    )
    db_session.add(review)
    await db_session.flush()
    return review


# ---- total / merged / merge_rate --------------------------------------------------


async def test_metrics_zero_prs(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, 1, "octocat")
    repository = await _make_tracked_repository(db_session, user, 100, "octocat/empty")

    result = await get_repository_metrics(repository.id, user, db_session)

    assert result.total_pull_requests == 0
    assert result.merged_pull_requests == 0
    assert result.merge_rate is None
    assert result.median_pr_cycle_time_hours is None
    assert result.median_time_to_first_review_hours is None


async def test_metrics_one_merged_pr(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, 2, "octocat")
    repository = await _make_tracked_repository(db_session, user, 101, "octocat/one-pr")
    await _add_pr(db_session, repository.id, 1001, 1, BASE, BASE + timedelta(hours=10))

    result = await get_repository_metrics(repository.id, user, db_session)

    assert result.total_pull_requests == 1
    assert result.merged_pull_requests == 1
    assert result.merge_rate == 1.0
    assert result.median_pr_cycle_time_hours == 10.0


async def test_metrics_multiple_prs_merge_rate(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, 3, "octocat")
    repository = await _make_tracked_repository(db_session, user, 102, "octocat/mixed")
    await _add_pr(db_session, repository.id, 1002, 1, BASE, BASE + timedelta(hours=5))
    await _add_pr(db_session, repository.id, 1003, 2, BASE, BASE + timedelta(hours=15))
    await _add_pr(db_session, repository.id, 1004, 3, BASE, merged_at=None)

    result = await get_repository_metrics(repository.id, user, db_session)

    assert result.total_pull_requests == 3
    assert result.merged_pull_requests == 2
    assert result.merge_rate == pytest.approx(2 / 3)


async def test_metrics_merge_rate_zero_when_none_merged(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, 4, "octocat")
    repository = await _make_tracked_repository(db_session, user, 103, "octocat/none-merged")
    await _add_pr(db_session, repository.id, 1005, 1, BASE, merged_at=None)
    await _add_pr(db_session, repository.id, 1006, 2, BASE, merged_at=None)

    result = await get_repository_metrics(repository.id, user, db_session)

    assert result.total_pull_requests == 2
    assert result.merged_pull_requests == 0
    assert result.merge_rate == 0.0
    assert result.median_pr_cycle_time_hours is None


# ---- PR cycle time -----------------------------------------------------------------


async def test_metrics_cycle_time_odd_count_median(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, 5, "octocat")
    repository = await _make_tracked_repository(db_session, user, 104, "octocat/odd-cycle")
    for i, hours in enumerate([10, 20, 30], start=1):
        await _add_pr(db_session, repository.id, 2000 + i, i, BASE, BASE + timedelta(hours=hours))

    result = await get_repository_metrics(repository.id, user, db_session)

    assert result.median_pr_cycle_time_hours == 20.0


async def test_metrics_cycle_time_even_count_median(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, 6, "octocat")
    repository = await _make_tracked_repository(db_session, user, 105, "octocat/even-cycle")
    for i, hours in enumerate([10, 20, 30, 40], start=1):
        await _add_pr(db_session, repository.id, 3000 + i, i, BASE, BASE + timedelta(hours=hours))

    result = await get_repository_metrics(repository.id, user, db_session)

    assert result.median_pr_cycle_time_hours == 25.0


async def test_metrics_excludes_malformed_negative_cycle_time(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, 7, "octocat")
    repository = await _make_tracked_repository(db_session, user, 106, "octocat/malformed-cycle")
    # merged_at before github_created_at -- malformed, not a real duration.
    await _add_pr(db_session, repository.id, 4001, 1, BASE, BASE - timedelta(hours=5))

    result = await get_repository_metrics(repository.id, user, db_session)

    # Still counted as "merged" by the raw definition (merged_at IS NOT NULL) ...
    assert result.merged_pull_requests == 1
    # ... but excluded from the cycle-time calculation itself.
    assert result.median_pr_cycle_time_hours is None


# ---- time to first review -----------------------------------------------------------


async def test_metrics_pr_with_no_reviews_excluded_from_first_review(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session, 8, "octocat")
    repository = await _make_tracked_repository(db_session, user, 107, "octocat/no-reviews")
    pr_with_review = await _add_pr(db_session, repository.id, 5001, 1, BASE)
    await _add_review(db_session, pr_with_review.id, 6001, submitted_at=BASE + timedelta(hours=4))
    await _add_pr(db_session, repository.id, 5002, 2, BASE)  # no reviews at all

    result = await get_repository_metrics(repository.id, user, db_session)

    assert result.median_time_to_first_review_hours == 4.0


async def test_metrics_earliest_of_multiple_reviews_selected(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, 9, "octocat")
    repository = await _make_tracked_repository(db_session, user, 108, "octocat/multi-review")
    pull_request = await _add_pr(db_session, repository.id, 5003, 1, BASE)
    await _add_review(db_session, pull_request.id, 6002, submitted_at=BASE + timedelta(hours=10))
    await _add_review(db_session, pull_request.id, 6003, submitted_at=BASE + timedelta(hours=5))
    await _add_review(db_session, pull_request.id, 6004, submitted_at=BASE + timedelta(hours=20))

    result = await get_repository_metrics(repository.id, user, db_session)

    assert result.median_time_to_first_review_hours == 5.0


@pytest.mark.parametrize(
    ("index", "state"),
    [(1, "APPROVED"), (2, "CHANGES_REQUESTED"), (3, "COMMENTED"), (4, "DISMISSED")],
)
async def test_metrics_review_state_qualifies(
    db_session: AsyncSession, index: int, state: str
) -> None:
    user = await _make_user(db_session, 10 + index, "octocat")
    repository = await _make_tracked_repository(
        db_session, user, 200 + index, f"octocat/{state.lower()}"
    )
    pull_request = await _add_pr(db_session, repository.id, 7000 + index, 1, BASE)
    await _add_review(
        db_session,
        pull_request.id,
        8000 + index,
        state=state,
        submitted_at=BASE + timedelta(hours=3),
    )

    result = await get_repository_metrics(repository.id, user, db_session)

    assert result.median_time_to_first_review_hours == 3.0


async def test_metrics_pending_null_submitted_at_excluded(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, 20, "octocat")
    repository = await _make_tracked_repository(db_session, user, 300, "octocat/pending-review")
    pull_request = await _add_pr(db_session, repository.id, 9001, 1, BASE)
    await _add_review(db_session, pull_request.id, 9101, state="PENDING", submitted_at=None)

    result = await get_repository_metrics(repository.id, user, db_session)

    assert result.median_time_to_first_review_hours is None


async def test_metrics_submitted_before_creation_excluded(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, 21, "octocat")
    repository = await _make_tracked_repository(db_session, user, 301, "octocat/backdated-review")
    pull_request = await _add_pr(db_session, repository.id, 9002, 1, BASE)
    await _add_review(db_session, pull_request.id, 9102, submitted_at=BASE - timedelta(hours=1))

    result = await get_repository_metrics(repository.id, user, db_session)

    assert result.median_time_to_first_review_hours is None


async def test_metrics_later_valid_review_used_when_earlier_review_is_malformed(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session, 22, "octocat")
    repository = await _make_tracked_repository(db_session, user, 302, "octocat/one-bad-one-good")
    pull_request = await _add_pr(db_session, repository.id, 9003, 1, BASE)
    # Malformed: submitted before the PR was even created.
    await _add_review(db_session, pull_request.id, 9103, submitted_at=BASE - timedelta(hours=2))
    # Valid, later review -- this is the one that should be used.
    await _add_review(db_session, pull_request.id, 9104, submitted_at=BASE + timedelta(hours=7))

    result = await get_repository_metrics(repository.id, user, db_session)

    assert result.median_time_to_first_review_hours == 7.0


async def test_metrics_first_review_odd_count_median(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, 23, "octocat")
    repository = await _make_tracked_repository(db_session, user, 303, "octocat/odd-review")
    for i, hours in enumerate([2, 6, 10], start=1):
        pull_request = await _add_pr(db_session, repository.id, 9200 + i, i, BASE)
        await _add_review(
            db_session, pull_request.id, 9300 + i, submitted_at=BASE + timedelta(hours=hours)
        )

    result = await get_repository_metrics(repository.id, user, db_session)

    assert result.median_time_to_first_review_hours == 6.0


async def test_metrics_first_review_even_count_median(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, 24, "octocat")
    repository = await _make_tracked_repository(db_session, user, 304, "octocat/even-review")
    for i, hours in enumerate([2, 4, 6, 8], start=1):
        pull_request = await _add_pr(db_session, repository.id, 9400 + i, i, BASE)
        await _add_review(
            db_session, pull_request.id, 9500 + i, submitted_at=BASE + timedelta(hours=hours)
        )

    result = await get_repository_metrics(repository.id, user, db_session)

    assert result.median_time_to_first_review_hours == 5.0


async def test_metrics_open_reviewed_pr_contributes_to_first_review(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session, 25, "octocat")
    repository = await _make_tracked_repository(db_session, user, 305, "octocat/open-reviewed")
    pull_request = await _add_pr(db_session, repository.id, 9600, 1, BASE, merged_at=None)
    await _add_review(db_session, pull_request.id, 9700, submitted_at=BASE + timedelta(hours=8))

    result = await get_repository_metrics(repository.id, user, db_session)

    assert result.merged_pull_requests == 0
    assert result.median_time_to_first_review_hours == 8.0


# ---- identity irrelevance / combined empty-population behavior ---------------------


async def test_metrics_login_identity_does_not_affect_computation(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, 26, "octocat")
    repository = await _make_tracked_repository(db_session, user, 306, "octocat/anon")
    pull_request = await _add_pr(
        db_session, repository.id, 9800, 1, BASE, BASE + timedelta(hours=6), author_login=None
    )
    await _add_review(
        db_session,
        pull_request.id,
        9900,
        submitted_at=BASE + timedelta(hours=2),
        reviewer_login=None,
    )

    result = await get_repository_metrics(repository.id, user, db_session)

    assert result.median_pr_cycle_time_hours == 6.0
    assert result.median_time_to_first_review_hours == 2.0


async def test_metrics_prs_exist_but_no_qualifying_reviews(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, 27, "octocat")
    repository = await _make_tracked_repository(db_session, user, 307, "octocat/no-qualifying")
    await _add_pr(db_session, repository.id, 9801, 1, BASE, merged_at=None)
    await _add_pr(db_session, repository.id, 9802, 2, BASE, merged_at=None)

    result = await get_repository_metrics(repository.id, user, db_session)

    assert result.total_pull_requests == 2
    assert result.merged_pull_requests == 0
    assert result.merge_rate == 0.0
    assert result.median_pr_cycle_time_hours is None
    assert result.median_time_to_first_review_hours is None
