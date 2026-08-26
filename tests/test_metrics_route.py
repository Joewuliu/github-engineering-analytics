from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.user import User
from app.models.user_repository import UserRepository

StubGitHub = Callable[[Callable[[httpx.Request], httpx.Response]], None]
AuthClient = tuple[AsyncClient, User]

BASE = datetime(2024, 1, 1, tzinfo=UTC)


async def _make_user(db_session: AsyncSession, github_id: int, login: str) -> User:
    user = User(github_id=github_id, github_login=login)
    db_session.add(user)
    await db_session.flush()
    return user


async def _track(db_session: AsyncSession, user: User, repository: Repository) -> None:
    db_session.add(repository)
    await db_session.flush()
    db_session.add(UserRepository(user_id=user.id, repository_id=repository.id))
    await db_session.commit()


async def test_metrics_requires_authentication(async_client: AsyncClient) -> None:
    response = await async_client.get("/me/repositories/1/metrics")
    assert response.status_code == 401


async def test_metrics_nonexistent_repository_returns_404(authenticated_client: AuthClient) -> None:
    client, _user = authenticated_client
    response = await client.get("/me/repositories/999999/metrics")
    assert response.status_code == 404


async def test_metrics_untracked_repository_returns_404(
    authenticated_client: AuthClient, db_session: AsyncSession
) -> None:
    client, _user = authenticated_client
    owner = await _make_user(db_session, 800101, "owner")
    repository = Repository(github_id=700, full_name="octocat/not-mine")
    await _track(db_session, owner, repository)

    response = await client.get(f"/me/repositories/{repository.id}/metrics")

    assert response.status_code == 404


async def test_metrics_tracked_but_never_synced_returns_empty_metrics(
    authenticated_client: AuthClient, db_session: AsyncSession
) -> None:
    client, user = authenticated_client
    repository = Repository(github_id=701, full_name="octocat/never-synced")
    await _track(db_session, user, repository)

    response = await client.get(f"/me/repositories/{repository.id}/metrics")

    assert response.status_code == 200
    assert response.json() == {
        "repository_id": repository.id,
        "full_name": "octocat/never-synced",
        "total_pull_requests": 0,
        "merged_pull_requests": 0,
        "merge_rate": None,
        "median_pr_cycle_time_hours": None,
        "median_time_to_first_review_hours": None,
    }


async def test_metrics_tracked_with_data_returns_correct_response(
    authenticated_client: AuthClient, db_session: AsyncSession
) -> None:
    client, user = authenticated_client
    repository = Repository(github_id=702, full_name="octocat/has-data")
    await _track(db_session, user, repository)

    db_session.add(
        PullRequest(
            github_id=10001,
            repository_id=repository.id,
            number=1,
            author_login="octocat",
            title="PR 1",
            state="closed",
            github_created_at=BASE,
            github_updated_at=BASE,
            merged_at=BASE + timedelta(hours=12),
        )
    )
    db_session.add(
        PullRequest(
            github_id=10002,
            repository_id=repository.id,
            number=2,
            author_login="octocat",
            title="PR 2",
            state="open",
            github_created_at=BASE,
            github_updated_at=BASE,
        )
    )
    await db_session.commit()

    response = await client.get(f"/me/repositories/{repository.id}/metrics")

    assert response.status_code == 200
    body = response.json()
    assert body["repository_id"] == repository.id
    assert body["full_name"] == "octocat/has-data"
    assert body["total_pull_requests"] == 2
    assert body["merged_pull_requests"] == 1
    assert body["merge_rate"] == 0.5
    assert body["median_pr_cycle_time_hours"] == 12.0
    assert body["median_time_to_first_review_hours"] is None


async def test_metrics_rounds_to_two_decimal_places(
    authenticated_client: AuthClient, db_session: AsyncSession
) -> None:
    client, user = authenticated_client
    repository = Repository(github_id=703, full_name="octocat/rounding")
    await _track(db_session, user, repository)

    # 5170 seconds = 1.436111... hours -> rounds to 1.44
    db_session.add(
        PullRequest(
            github_id=10003,
            repository_id=repository.id,
            number=1,
            author_login="octocat",
            title="PR",
            state="closed",
            github_created_at=BASE,
            github_updated_at=BASE,
            merged_at=BASE + timedelta(seconds=5170),
        )
    )
    await db_session.commit()

    response = await client.get(f"/me/repositories/{repository.id}/metrics")

    assert response.status_code == 200
    assert response.json()["median_pr_cycle_time_hours"] == 1.44
    assert response.json()["merge_rate"] == 1.0


async def test_metrics_makes_no_github_calls(
    authenticated_client: AuthClient, stub_github_client: StubGitHub, db_session: AsyncSession
) -> None:
    client, user = authenticated_client
    repository = Repository(github_id=704, full_name="octocat/no-github-calls")
    await _track(db_session, user, repository)

    def trap_handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Metrics endpoint must never call GitHub: {request.url}")

    stub_github_client(trap_handler)

    response = await client.get(f"/me/repositories/{repository.id}/metrics")

    assert response.status_code == 200
