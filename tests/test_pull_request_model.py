from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pull_request import PullRequest
from app.models.pull_request_review import PullRequestReview
from app.models.repository import Repository

_NOW = datetime(2024, 1, 1, tzinfo=UTC)


async def _make_repository(db_session: AsyncSession, github_id: int, full_name: str) -> Repository:
    repository = Repository(github_id=github_id, full_name=full_name)
    db_session.add(repository)
    await db_session.flush()
    return repository


def _pull_request(
    repository_id: int, github_id: int, number: int, **overrides: object
) -> PullRequest:
    defaults: dict[str, object] = {
        "repository_id": repository_id,
        "github_id": github_id,
        "number": number,
        "author_login": "octocat",
        "title": "A pull request",
        "state": "open",
        "github_created_at": _NOW,
        "github_updated_at": _NOW,
    }
    defaults.update(overrides)
    return PullRequest(**defaults)  # type: ignore[arg-type]


async def test_pull_request_github_id_must_be_unique(db_session: AsyncSession) -> None:
    repository = await _make_repository(db_session, 1, "octocat/repo-a")
    db_session.add(_pull_request(repository.id, github_id=100, number=1))
    await db_session.commit()

    db_session.add(_pull_request(repository.id, github_id=100, number=2))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_pull_request_repository_id_and_number_must_be_unique(
    db_session: AsyncSession,
) -> None:
    repository = await _make_repository(db_session, 2, "octocat/repo-b")
    db_session.add(_pull_request(repository.id, github_id=101, number=5))
    await db_session.commit()

    db_session.add(_pull_request(repository.id, github_id=102, number=5))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_review_github_id_must_be_unique(db_session: AsyncSession) -> None:
    repository = await _make_repository(db_session, 3, "octocat/repo-c")
    pull_request = _pull_request(repository.id, github_id=103, number=1)
    db_session.add(pull_request)
    await db_session.flush()

    db_session.add(
        PullRequestReview(
            github_id=200, pull_request_id=pull_request.id, reviewer_login="rev", state="APPROVED"
        )
    )
    await db_session.commit()

    db_session.add(
        PullRequestReview(
            github_id=200, pull_request_id=pull_request.id, reviewer_login="rev2", state="COMMENTED"
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_deleting_repository_cascades_pull_requests_and_reviews(
    db_session: AsyncSession,
) -> None:
    repository = await _make_repository(db_session, 4, "octocat/repo-d")
    pull_request = _pull_request(repository.id, github_id=104, number=1)
    db_session.add(pull_request)
    await db_session.flush()
    db_session.add(
        PullRequestReview(
            github_id=201, pull_request_id=pull_request.id, reviewer_login="rev", state="APPROVED"
        )
    )
    await db_session.commit()

    await db_session.delete(repository)
    await db_session.commit()

    pr_result = await db_session.execute(
        select(PullRequest).where(PullRequest.repository_id == repository.id)
    )
    assert pr_result.scalar_one_or_none() is None

    review_result = await db_session.execute(
        select(PullRequestReview).where(PullRequestReview.pull_request_id == pull_request.id)
    )
    assert review_result.scalar_one_or_none() is None


async def test_deleting_pull_request_cascades_reviews(db_session: AsyncSession) -> None:
    repository = await _make_repository(db_session, 5, "octocat/repo-e")
    pull_request = _pull_request(repository.id, github_id=105, number=1)
    db_session.add(pull_request)
    await db_session.flush()
    db_session.add(
        PullRequestReview(
            github_id=202, pull_request_id=pull_request.id, reviewer_login="rev", state="APPROVED"
        )
    )
    await db_session.commit()

    await db_session.delete(pull_request)
    await db_session.commit()

    review_result = await db_session.execute(
        select(PullRequestReview).where(PullRequestReview.pull_request_id == pull_request.id)
    )
    assert review_result.scalar_one_or_none() is None

    # The canonical Repository is untouched by a PullRequest deletion.
    repo_result = await db_session.execute(select(Repository).where(Repository.id == repository.id))
    assert repo_result.scalar_one() is not None
