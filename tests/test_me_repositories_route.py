from collections.abc import Callable

import httpx
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.repository import Repository
from app.models.user import User
from app.models.user_repository import UserRepository

StubGitHub = Callable[[Callable[[httpx.Request], httpx.Response]], None]
AuthClient = tuple[AsyncClient, User]


def _json_handler(status_code: int, **kwargs: object) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, **kwargs)  # type: ignore[arg-type]

    return handler


async def _make_user(db_session: AsyncSession, github_id: int, login: str) -> User:
    user = User(github_id=github_id, github_login=login)
    db_session.add(user)
    await db_session.flush()
    return user


# ---- POST /me/repositories ------------------------------------------------------


async def test_post_me_repositories_requires_authentication(
    async_client: AsyncClient, stub_github_client: StubGitHub
) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"id": 1, "full_name": "x/y"})

    stub_github_client(handler)

    response = await async_client.post(
        "/me/repositories", json={"full_name": "octocat/hello-world"}
    )

    assert response.status_code == 401
    assert calls == []


async def test_post_me_repositories_new_repo_returns_201(
    authenticated_client: AuthClient, stub_github_client: StubGitHub
) -> None:
    client, _user = authenticated_client
    stub_github_client(_json_handler(200, json={"id": 1001, "full_name": "octocat/hello-world"}))

    response = await client.post("/me/repositories", json={"full_name": "octocat/hello-world"})

    assert response.status_code == 201
    body = response.json()
    assert body["github_id"] == 1001
    assert body["full_name"] == "octocat/hello-world"


async def test_post_me_repositories_existing_global_repo_new_user_returns_201(
    authenticated_client: AuthClient, stub_github_client: StubGitHub, db_session: AsyncSession
) -> None:
    client, user = authenticated_client
    other_user = await _make_user(db_session, 700001, "other-user")
    repository = Repository(github_id=1002, full_name="octocat/already-global")
    db_session.add(repository)
    await db_session.flush()
    db_session.add(UserRepository(user_id=other_user.id, repository_id=repository.id))
    await db_session.commit()

    stub_github_client(_json_handler(200, json={"id": 1002, "full_name": "octocat/already-global"}))

    response = await client.post("/me/repositories", json={"full_name": "octocat/already-global"})

    assert response.status_code == 201

    repos = await db_session.execute(select(Repository).where(Repository.github_id == 1002))
    assert len(repos.scalars().all()) == 1

    trackers = await db_session.execute(
        select(UserRepository).where(UserRepository.repository_id == repository.id)
    )
    assert {t.user_id for t in trackers.scalars().all()} == {other_user.id, user.id}


async def test_post_me_repositories_same_user_twice_returns_409(
    authenticated_client: AuthClient, stub_github_client: StubGitHub
) -> None:
    client, _user = authenticated_client
    stub_github_client(_json_handler(200, json={"id": 1003, "full_name": "octocat/duplicate"}))

    first = await client.post("/me/repositories", json={"full_name": "octocat/duplicate"})
    assert first.status_code == 201

    second = await client.post("/me/repositories", json={"full_name": "octocat/duplicate"})
    assert second.status_code == 409
    assert "token" not in second.text.lower()


async def test_post_me_repositories_invalid_full_name_returns_422_without_calling_github(
    authenticated_client: AuthClient, stub_github_client: StubGitHub
) -> None:
    client, _user = authenticated_client
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"id": 1, "full_name": "x/y"})

    stub_github_client(handler)

    response = await client.post("/me/repositories", json={"full_name": "no-slash-here"})

    assert response.status_code == 422
    assert calls == []


async def test_post_me_repositories_github_404_returns_404(
    authenticated_client: AuthClient, stub_github_client: StubGitHub
) -> None:
    client, _user = authenticated_client
    stub_github_client(_json_handler(404, json={"message": "Not Found"}))

    response = await client.post("/me/repositories", json={"full_name": "octocat/missing"})

    assert response.status_code == 404


async def test_post_me_repositories_github_auth_failure_returns_502(
    authenticated_client: AuthClient, stub_github_client: StubGitHub
) -> None:
    client, _user = authenticated_client
    stub_github_client(_json_handler(401, json={"message": "Bad credentials"}))

    response = await client.post("/me/repositories", json={"full_name": "octocat/hello-world"})

    assert response.status_code == 502


async def test_post_me_repositories_github_rate_limit_returns_503(
    authenticated_client: AuthClient, stub_github_client: StubGitHub
) -> None:
    client, _user = authenticated_client
    stub_github_client(
        _json_handler(403, headers={"X-RateLimit-Remaining": "0"}, json={"message": "rate limit"})
    )

    response = await client.post("/me/repositories", json={"full_name": "octocat/hello-world"})

    assert response.status_code == 503


async def test_post_me_repositories_github_5xx_returns_502(
    authenticated_client: AuthClient, stub_github_client: StubGitHub
) -> None:
    client, _user = authenticated_client
    stub_github_client(_json_handler(502, json={"message": "bad gateway"}))

    response = await client.post("/me/repositories", json={"full_name": "octocat/hello-world"})

    assert response.status_code == 502


async def test_post_me_repositories_github_timeout_returns_504(
    authenticated_client: AuthClient, stub_github_client: StubGitHub
) -> None:
    client, _user = authenticated_client

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    stub_github_client(handler)

    response = await client.post("/me/repositories", json={"full_name": "octocat/hello-world"})

    assert response.status_code == 504


# ---- GET /me/repositories ---------------------------------------------------------


async def test_get_me_repositories_requires_authentication(async_client: AsyncClient) -> None:
    response = await async_client.get("/me/repositories")
    assert response.status_code == 401


async def test_get_me_repositories_empty_for_user_with_none(
    authenticated_client: AuthClient,
) -> None:
    client, _user = authenticated_client
    response = await client.get("/me/repositories")
    assert response.status_code == 200
    assert response.json() == []


async def test_get_me_repositories_returns_only_current_users_repositories(
    authenticated_client: AuthClient, db_session: AsyncSession
) -> None:
    client, user = authenticated_client
    other_user = await _make_user(db_session, 700002, "other-user")

    mine = Repository(github_id=2001, full_name="octocat/mine")
    theirs = Repository(github_id=2002, full_name="octocat/theirs")
    db_session.add_all([mine, theirs])
    await db_session.flush()
    db_session.add(UserRepository(user_id=user.id, repository_id=mine.id))
    db_session.add(UserRepository(user_id=other_user.id, repository_id=theirs.id))
    await db_session.commit()

    response = await client.get("/me/repositories")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["github_id"] == 2001
    assert "tracked_at" in body[0]


async def test_get_me_repositories_orders_by_tracked_at_desc_with_tiebreak(
    authenticated_client: AuthClient, db_session: AsyncSession
) -> None:
    client, user = authenticated_client

    repo_a = Repository(github_id=2003, full_name="octocat/a")
    repo_b = Repository(github_id=2004, full_name="octocat/b")
    db_session.add_all([repo_a, repo_b])
    await db_session.flush()
    # Same tracked_at instant is plausible (server_default=now() at nearly the
    # same moment) -- the repository_id DESC tiebreak must still be stable.
    db_session.add(UserRepository(user_id=user.id, repository_id=repo_a.id))
    db_session.add(UserRepository(user_id=user.id, repository_id=repo_b.id))
    await db_session.commit()

    response = await client.get("/me/repositories")

    assert response.status_code == 200
    ids = [row["github_id"] for row in response.json()]
    assert ids == [2004, 2003]


# ---- DELETE /me/repositories/{repository_id} ---------------------------------------


async def test_delete_me_repositories_requires_authentication(async_client: AsyncClient) -> None:
    response = await async_client.delete("/me/repositories/1")
    assert response.status_code == 401


async def test_delete_me_repositories_removes_tracking_and_keeps_repository(
    authenticated_client: AuthClient, stub_github_client: StubGitHub, db_session: AsyncSession
) -> None:
    client, _user = authenticated_client
    stub_github_client(_json_handler(200, json={"id": 3001, "full_name": "octocat/to-untrack"}))
    created = await client.post("/me/repositories", json={"full_name": "octocat/to-untrack"})
    repository_id = created.json()["id"]

    response = await client.delete(f"/me/repositories/{repository_id}")

    assert response.status_code == 204

    tracking = await db_session.execute(
        select(UserRepository).where(UserRepository.repository_id == repository_id)
    )
    assert tracking.scalar_one_or_none() is None

    repo = await db_session.execute(select(Repository).where(Repository.id == repository_id))
    assert repo.scalar_one() is not None


async def test_delete_me_repositories_twice_is_idempotent(
    authenticated_client: AuthClient, stub_github_client: StubGitHub
) -> None:
    client, _user = authenticated_client
    stub_github_client(_json_handler(200, json={"id": 3002, "full_name": "octocat/repeat-delete"}))
    created = await client.post("/me/repositories", json={"full_name": "octocat/repeat-delete"})
    repository_id = created.json()["id"]

    first = await client.delete(f"/me/repositories/{repository_id}")
    second = await client.delete(f"/me/repositories/{repository_id}")

    assert first.status_code == 204
    assert second.status_code == 204


async def test_delete_me_repositories_nonexistent_id_returns_204(
    authenticated_client: AuthClient,
) -> None:
    client, _user = authenticated_client
    response = await client.delete("/me/repositories/999999")
    assert response.status_code == 204


async def test_delete_me_repositories_does_not_affect_another_users_tracking(
    authenticated_client: AuthClient, db_session: AsyncSession
) -> None:
    client, user = authenticated_client
    other_user = await _make_user(db_session, 700003, "other-user")
    repository = Repository(github_id=3003, full_name="octocat/shared-untrack")
    db_session.add(repository)
    await db_session.flush()
    db_session.add(UserRepository(user_id=user.id, repository_id=repository.id))
    db_session.add(UserRepository(user_id=other_user.id, repository_id=repository.id))
    await db_session.commit()

    response = await client.delete(f"/me/repositories/{repository.id}")

    assert response.status_code == 204

    remaining = await db_session.execute(
        select(UserRepository).where(UserRepository.repository_id == repository.id)
    )
    assert [a.user_id for a in remaining.scalars().all()] == [other_user.id]
