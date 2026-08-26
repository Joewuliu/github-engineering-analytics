from datetime import UTC, datetime
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.github.client import GitHubClient
from app.models.pull_request import PullRequest
from app.models.pull_request_review import PullRequestReview
from app.models.repository import Repository
from app.models.user import User
from app.models.user_repository import UserRepository
from app.services.repositories import RepositoryNotTrackedError
from app.services.repository_sync import sync_repository


def _pr(id_: int, number: int, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": id_,
        "number": number,
        "title": f"PR {number}",
        "state": "open",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-02T00:00:00Z",
        "closed_at": None,
        "merged_at": None,
        "user": {"login": "octocat"},
    }
    payload.update(overrides)
    return payload


def _review(id_: int, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": id_,
        "state": "APPROVED",
        "submitted_at": "2024-01-03T00:00:00Z",
        "user": {"login": "reviewer"},
    }
    payload.update(overrides)
    return payload


def _github_client(
    prs: list[dict[str, object]],
    reviews_by_number: dict[int, list[dict[str, object]]] | None = None,
    captured: list[httpx.Request] | None = None,
) -> GitHubClient:
    reviews_by_number = reviews_by_number or {}

    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        path = request.url.path
        if path.endswith("/reviews"):
            number = int(path.split("/")[-2])
            return httpx.Response(200, json=reviews_by_number.get(number, []))
        if path.endswith("/pulls"):
            return httpx.Response(200, json=prs)
        return httpx.Response(404, json={"message": "not found"})

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.github.com"
    )
    return GitHubClient(http_client)


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


async def test_sync_repository_raises_when_repository_does_not_exist(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session, 1, "octocat")
    github_client = _github_client([])

    with pytest.raises(RepositoryNotTrackedError):
        await sync_repository(999999, user, db_session, github_client)


async def test_sync_repository_raises_when_not_tracked_by_current_user(
    db_session: AsyncSession,
) -> None:
    owner = await _make_user(db_session, 2, "owner")
    other = await _make_user(db_session, 3, "other")
    repository = await _make_tracked_repository(db_session, owner, 500, "octocat/private-ish")
    github_client = _github_client([])

    with pytest.raises(RepositoryNotTrackedError):
        await sync_repository(repository.id, other, db_session, github_client)


async def test_sync_repository_first_sync_inserts_prs_and_reviews(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, 4, "octocat")
    repository = await _make_tracked_repository(db_session, user, 501, "octocat/repo-a")
    github_client = _github_client(
        [_pr(1001, 1), _pr(1002, 2)],
        {1: [_review(2001)], 2: [_review(2002), _review(2003)]},
    )

    result_repo, pr_count, review_count = await sync_repository(
        repository.id, user, db_session, github_client
    )

    assert result_repo.id == repository.id
    assert pr_count == 2
    assert review_count == 3

    prs = await db_session.execute(
        select(PullRequest).where(PullRequest.repository_id == repository.id)
    )
    assert len(prs.scalars().all()) == 2

    reviews = await db_session.execute(select(PullRequestReview))
    assert len(reviews.scalars().all()) == 3


async def test_sync_repository_repeated_identical_sync_creates_no_duplicates(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session, 5, "octocat")
    repository = await _make_tracked_repository(db_session, user, 502, "octocat/repo-b")
    github_client = _github_client([_pr(1003, 1)], {1: [_review(2004)]})

    await sync_repository(repository.id, user, db_session, github_client)
    await sync_repository(repository.id, user, db_session, github_client)

    prs = await db_session.execute(
        select(PullRequest).where(PullRequest.repository_id == repository.id)
    )
    assert len(prs.scalars().all()) == 1

    reviews = await db_session.execute(select(PullRequestReview))
    assert len(reviews.scalars().all()) == 1


async def test_sync_repository_updates_mutable_pr_fields(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, 6, "octocat")
    repository = await _make_tracked_repository(db_session, user, 503, "octocat/repo-c")

    first_client = _github_client([_pr(1004, 1, state="open", title="WIP")])
    await sync_repository(repository.id, user, db_session, first_client)

    second_client = _github_client(
        [
            _pr(
                1004,
                1,
                state="closed",
                title="Ready to merge",
                merged_at="2024-01-05T00:00:00Z",
                closed_at="2024-01-05T00:00:00Z",
            )
        ]
    )
    await sync_repository(repository.id, user, db_session, second_client)

    result = await db_session.execute(select(PullRequest).where(PullRequest.github_id == 1004))
    pull_request = result.scalar_one()
    assert pull_request.state == "closed"
    assert pull_request.title == "Ready to merge"
    assert pull_request.merged_at is not None
    # Identity/history fields must never change.
    assert pull_request.number == 1
    assert pull_request.repository_id == repository.id


async def test_sync_repository_updates_author_login(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, 7, "octocat")
    repository = await _make_tracked_repository(db_session, user, 504, "octocat/repo-d")

    await sync_repository(
        repository.id,
        user,
        db_session,
        _github_client([_pr(1005, 1, user={"login": "old-name"})]),
    )
    await sync_repository(
        repository.id,
        user,
        db_session,
        _github_client([_pr(1005, 1, user={"login": "new-name"})]),
    )

    result = await db_session.execute(select(PullRequest).where(PullRequest.github_id == 1005))
    assert result.scalar_one().author_login == "new-name"


async def test_sync_repository_updates_review_state_and_reviewer_login(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session, 8, "octocat")
    repository = await _make_tracked_repository(db_session, user, 505, "octocat/repo-e")

    await sync_repository(
        repository.id,
        user,
        db_session,
        _github_client(
            [_pr(1006, 1)], {1: [_review(2005, state="APPROVED", user={"login": "old-reviewer"})]}
        ),
    )
    await sync_repository(
        repository.id,
        user,
        db_session,
        _github_client(
            [_pr(1006, 1)],
            {1: [_review(2005, state="DISMISSED", user={"login": "new-reviewer"})]},
        ),
    )

    result = await db_session.execute(
        select(PullRequestReview).where(PullRequestReview.github_id == 2005)
    )
    review = result.scalar_one()
    assert review.state == "DISMISSED"
    assert review.reviewer_login == "new-reviewer"


async def test_sync_repository_inserts_review_added_on_later_sync(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, 9, "octocat")
    repository = await _make_tracked_repository(db_session, user, 506, "octocat/repo-f")

    await sync_repository(repository.id, user, db_session, _github_client([_pr(1007, 1)], {1: []}))
    await sync_repository(
        repository.id, user, db_session, _github_client([_pr(1007, 1)], {1: [_review(2006)]})
    )

    result = await db_session.execute(
        select(PullRequestReview).where(PullRequestReview.github_id == 2006)
    )
    assert result.scalar_one() is not None


async def test_sync_repository_requests_bounded_per_page(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, 10, "octocat")
    repository = await _make_tracked_repository(db_session, user, 507, "octocat/repo-g")
    captured: list[httpx.Request] = []
    github_client = _github_client([], captured=captured)

    await sync_repository(repository.id, user, db_session, github_client)

    pulls_request = next(r for r in captured if r.url.path.endswith("/pulls"))
    assert pulls_request.url.params["per_page"] == "25"


async def test_sync_repository_rolls_back_on_persistence_failure(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, 11, "octocat")
    repository = await _make_tracked_repository(db_session, user, 508, "octocat/repo-h")
    repository_id = repository.id
    github_client = _github_client([_pr(1008, 1)], {1: [_review(2007)]})

    with (
        patch.object(
            db_session, "commit", side_effect=IntegrityError("stmt", {}, Exception("boom"))
        ),
        pytest.raises(IntegrityError),
    ):
        await sync_repository(repository_id, user, db_session, github_client)

    # sync_repository deliberately does not catch/rollback a commit failure
    # itself -- in the real request lifecycle, get_db()'s dependency rolls
    # back on any unhandled exception. Mirror that here before asserting
    # nothing was actually persisted. A rollback expires already-loaded ORM
    # objects, so only the plain repository_id int (captured above) is used
    # from here on, not attribute access on the `repository` object itself.
    await db_session.rollback()

    prs = await db_session.execute(
        select(PullRequest).where(PullRequest.repository_id == repository_id)
    )
    assert prs.scalar_one_or_none() is None


async def test_sync_repository_number_collision_with_different_github_id_raises(
    db_session: AsyncSession,
) -> None:
    # A PR already exists at (repository, number=1) under one github_id.
    # GitHub now reports a *different* github_id claiming the same number --
    # this is not the identity-conflict ON CONFLICT target handles, so it
    # must surface as a genuine, uncaught IntegrityError rather than silently
    # rewriting identity.
    user = await _make_user(db_session, 12, "octocat")
    repository = await _make_tracked_repository(db_session, user, 509, "octocat/repo-i")
    db_session.add(
        PullRequest(
            github_id=9001,
            repository_id=repository.id,
            number=1,
            author_login="octocat",
            title="original",
            state="open",
            github_created_at=datetime(2024, 1, 1, tzinfo=UTC),
            github_updated_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
    )
    await db_session.commit()

    github_client = _github_client([_pr(9002, 1)])

    with pytest.raises(IntegrityError):
        await sync_repository(repository.id, user, db_session, github_client)
