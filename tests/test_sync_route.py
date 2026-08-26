from collections.abc import Callable

import httpx
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.repository import Repository
from app.models.user import User
from app.models.user_repository import UserRepository

StubGitHub = Callable[[Callable[[httpx.Request], httpx.Response]], None]
AuthClient = tuple[AsyncClient, User]


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


def _sync_handler(
    prs: list[dict[str, object]],
    reviews_by_number: dict[int, list[dict[str, object]]] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    reviews_by_number = reviews_by_number or {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/reviews"):
            number = int(path.split("/")[-2])
            return httpx.Response(200, json=reviews_by_number.get(number, []))
        if path.endswith("/pulls"):
            return httpx.Response(200, json=prs)
        return httpx.Response(404, json={"message": "not found"})

    return handler


async def _make_user(db_session: AsyncSession, github_id: int, login: str) -> User:
    user = User(github_id=github_id, github_login=login)
    db_session.add(user)
    await db_session.flush()
    return user


async def test_sync_requires_authentication(
    async_client: AsyncClient, stub_github_client: StubGitHub
) -> None:
    stub_github_client(_sync_handler([]))

    response = await async_client.post("/me/repositories/1/sync")

    assert response.status_code == 401


async def test_sync_nonexistent_repository_returns_404(
    authenticated_client: AuthClient, stub_github_client: StubGitHub
) -> None:
    client, _user = authenticated_client
    stub_github_client(_sync_handler([]))

    response = await client.post("/me/repositories/999999/sync")

    assert response.status_code == 404


async def test_sync_untracked_repository_returns_404(
    authenticated_client: AuthClient, stub_github_client: StubGitHub, db_session: AsyncSession
) -> None:
    client, _user = authenticated_client
    owner = await _make_user(db_session, 800001, "owner")
    repository = Repository(github_id=600, full_name="octocat/not-mine")
    db_session.add(repository)
    await db_session.flush()
    db_session.add(UserRepository(user_id=owner.id, repository_id=repository.id))
    await db_session.commit()

    stub_github_client(_sync_handler([]))

    response = await client.post(f"/me/repositories/{repository.id}/sync")

    assert response.status_code == 404


async def test_sync_tracked_repository_succeeds_with_correct_summary(
    authenticated_client: AuthClient, stub_github_client: StubGitHub, db_session: AsyncSession
) -> None:
    client, user = authenticated_client
    repository = Repository(github_id=601, full_name="octocat/tracked-repo")
    db_session.add(repository)
    await db_session.flush()
    db_session.add(UserRepository(user_id=user.id, repository_id=repository.id))
    await db_session.commit()

    stub_github_client(
        _sync_handler(
            [_pr(3001, 1), _pr(3002, 2)],
            {1: [_review(4001)], 2: [_review(4002), _review(4003)]},
        )
    )

    response = await client.post(f"/me/repositories/{repository.id}/sync")

    assert response.status_code == 200
    body = response.json()
    assert body["repository_id"] == repository.id
    assert body["full_name"] == "octocat/tracked-repo"
    assert body["pull_requests_processed"] == 2
    assert body["reviews_processed"] == 3


async def test_sync_github_404_returns_404(
    authenticated_client: AuthClient, stub_github_client: StubGitHub, db_session: AsyncSession
) -> None:
    client, user = authenticated_client
    repository = Repository(github_id=602, full_name="octocat/deleted-upstream")
    db_session.add(repository)
    await db_session.flush()
    db_session.add(UserRepository(user_id=user.id, repository_id=repository.id))
    await db_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    stub_github_client(handler)

    response = await client.post(f"/me/repositories/{repository.id}/sync")

    assert response.status_code == 404


async def test_sync_github_rate_limit_returns_503(
    authenticated_client: AuthClient, stub_github_client: StubGitHub, db_session: AsyncSession
) -> None:
    client, user = authenticated_client
    repository = Repository(github_id=603, full_name="octocat/rate-limited")
    db_session.add(repository)
    await db_session.flush()
    db_session.add(UserRepository(user_id=user.id, repository_id=repository.id))
    await db_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers={"X-RateLimit-Remaining": "0"}, json={})

    stub_github_client(handler)

    response = await client.post(f"/me/repositories/{repository.id}/sync")

    assert response.status_code == 503


async def test_sync_github_timeout_returns_504(
    authenticated_client: AuthClient, stub_github_client: StubGitHub, db_session: AsyncSession
) -> None:
    client, user = authenticated_client
    repository = Repository(github_id=604, full_name="octocat/slow-upstream")
    db_session.add(repository)
    await db_session.flush()
    db_session.add(UserRepository(user_id=user.id, repository_id=repository.id))
    await db_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    stub_github_client(handler)

    response = await client.post(f"/me/repositories/{repository.id}/sync")

    assert response.status_code == 504


async def test_sync_malformed_github_response_returns_502(
    authenticated_client: AuthClient, stub_github_client: StubGitHub, db_session: AsyncSession
) -> None:
    client, user = authenticated_client
    repository = Repository(github_id=605, full_name="octocat/malformed")
    db_session.add(repository)
    await db_session.flush()
    db_session.add(UserRepository(user_id=user.id, repository_id=repository.id))
    await db_session.commit()

    stub_github_client(_sync_handler([{"id": 1}]))  # missing required fields

    response = await client.post(f"/me/repositories/{repository.id}/sync")

    assert response.status_code == 502
