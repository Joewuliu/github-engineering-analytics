from collections.abc import Callable

import httpx
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.repository import Repository

StubGitHub = Callable[[Callable[[httpx.Request], httpx.Response]], None]


def _json_handler(status_code: int, **kwargs: object) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, **kwargs)  # type: ignore[arg-type]

    return handler


async def test_post_repositories_returns_201_with_created_repository(
    async_client: AsyncClient, stub_github_client: StubGitHub
) -> None:
    stub_github_client(_json_handler(200, json={"id": 1001, "full_name": "octocat/hello-world"}))

    response = await async_client.post("/repositories", json={"full_name": "octocat/hello-world"})

    assert response.status_code == 201
    body = response.json()
    assert body["github_id"] == 1001
    assert body["full_name"] == "octocat/hello-world"
    assert "id" in body
    assert "created_at" in body
    assert "updated_at" in body


async def test_post_repositories_persists_repository(
    async_client: AsyncClient, stub_github_client: StubGitHub, db_session: AsyncSession
) -> None:
    stub_github_client(_json_handler(200, json={"id": 1002, "full_name": "octocat/persisted"}))

    await async_client.post("/repositories", json={"full_name": "octocat/persisted"})

    result = await db_session.execute(select(Repository).where(Repository.github_id == 1002))
    assert result.scalar_one().full_name == "octocat/persisted"


async def test_post_repositories_invalid_full_name_returns_422_without_calling_github(
    async_client: AsyncClient, stub_github_client: StubGitHub
) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"id": 1, "full_name": "x/y"})

    stub_github_client(handler)

    response = await async_client.post("/repositories", json={"full_name": "no-slash-here"})

    assert response.status_code == 422
    assert calls == []


async def test_post_repositories_duplicate_returns_409(
    async_client: AsyncClient, stub_github_client: StubGitHub, db_session: AsyncSession
) -> None:
    db_session.add(Repository(github_id=1003, full_name="octocat/duplicate"))
    await db_session.commit()

    stub_github_client(_json_handler(200, json={"id": 1003, "full_name": "octocat/duplicate"}))

    response = await async_client.post("/repositories", json={"full_name": "octocat/duplicate"})

    assert response.status_code == 409
    assert "token" not in response.text.lower()


async def test_post_repositories_github_404_returns_404(
    async_client: AsyncClient, stub_github_client: StubGitHub
) -> None:
    stub_github_client(_json_handler(404, json={"message": "Not Found"}))

    response = await async_client.post("/repositories", json={"full_name": "octocat/missing"})

    assert response.status_code == 404


async def test_post_repositories_github_auth_failure_returns_502(
    async_client: AsyncClient, stub_github_client: StubGitHub
) -> None:
    stub_github_client(_json_handler(401, json={"message": "Bad credentials"}))

    response = await async_client.post("/repositories", json={"full_name": "octocat/hello-world"})

    assert response.status_code == 502
    assert "credentials" not in response.text.lower()
    assert "bad credentials" not in response.text.lower()


async def test_post_repositories_github_rate_limit_returns_503(
    async_client: AsyncClient, stub_github_client: StubGitHub
) -> None:
    stub_github_client(
        _json_handler(403, headers={"X-RateLimit-Remaining": "0"}, json={"message": "rate limit"})
    )

    response = await async_client.post("/repositories", json={"full_name": "octocat/hello-world"})

    assert response.status_code == 503


async def test_post_repositories_github_5xx_returns_502(
    async_client: AsyncClient, stub_github_client: StubGitHub
) -> None:
    stub_github_client(_json_handler(502, json={"message": "bad gateway"}))

    response = await async_client.post("/repositories", json={"full_name": "octocat/hello-world"})

    assert response.status_code == 502


async def test_post_repositories_github_timeout_returns_504(
    async_client: AsyncClient, stub_github_client: StubGitHub
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    stub_github_client(handler)

    response = await async_client.post("/repositories", json={"full_name": "octocat/hello-world"})

    assert response.status_code == 504


async def test_post_repositories_github_connection_failure_returns_502(
    async_client: AsyncClient, stub_github_client: StubGitHub
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    stub_github_client(handler)

    response = await async_client.post("/repositories", json={"full_name": "octocat/hello-world"})

    assert response.status_code == 502


async def test_post_repositories_malformed_github_response_returns_502(
    async_client: AsyncClient, stub_github_client: StubGitHub
) -> None:
    stub_github_client(_json_handler(200, json={"full_name": "octocat/hello-world"}))

    response = await async_client.post("/repositories", json={"full_name": "octocat/hello-world"})

    assert response.status_code == 502


async def test_get_repositories_still_works(async_client: AsyncClient) -> None:
    response = await async_client.get("/repositories")
    assert response.status_code == 200
    assert response.json() == []
