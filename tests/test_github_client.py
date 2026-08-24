import httpx
import pytest

from app.github.client import GitHubClient
from app.github.errors import (
    GitHubAuthenticationError,
    GitHubConnectionError,
    GitHubRateLimitError,
    GitHubRepositoryNotFoundError,
    GitHubResponseError,
    GitHubServerError,
    GitHubTimeoutError,
)


def _client(transport: httpx.MockTransport, *, token: str | None = None) -> GitHubClient:
    http_client = httpx.AsyncClient(transport=transport, base_url="https://api.github.com")
    return GitHubClient(http_client, token=token)


async def test_get_repository_returns_parsed_repository() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = {"id": 123, "full_name": "octocat/hello-world", "extra": True}
        return httpx.Response(200, json=payload)

    client = _client(httpx.MockTransport(handler))

    repository = await client.get_repository("octocat/hello-world")

    assert repository.id == 123
    assert repository.full_name == "octocat/hello-world"


async def test_get_repository_sends_authorization_header_when_token_configured() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"id": 1, "full_name": "octocat/hello-world"})

    client = _client(httpx.MockTransport(handler), token="secret-token")
    await client.get_repository("octocat/hello-world")

    assert captured[0].headers["authorization"] == "Bearer secret-token"


async def test_get_repository_omits_authorization_header_when_no_token() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"id": 1, "full_name": "octocat/hello-world"})

    client = _client(httpx.MockTransport(handler), token=None)
    await client.get_repository("octocat/hello-world")

    assert "authorization" not in captured[0].headers


async def test_get_repository_404_raises_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    client = _client(httpx.MockTransport(handler))

    with pytest.raises(GitHubRepositoryNotFoundError):
        await client.get_repository("octocat/does-not-exist")


async def test_get_repository_401_raises_authentication_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    client = _client(httpx.MockTransport(handler), token="bad-token")

    with pytest.raises(GitHubAuthenticationError):
        await client.get_repository("octocat/hello-world")


async def test_get_repository_403_with_remaining_zero_raises_rate_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1700000000"},
            json={"message": "API rate limit exceeded"},
        )

    client = _client(httpx.MockTransport(handler))

    with pytest.raises(GitHubRateLimitError):
        await client.get_repository("octocat/hello-world")


async def test_get_repository_403_with_retry_after_raises_rate_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            headers={"Retry-After": "30"},
            json={"message": "secondary rate limit"},
        )

    client = _client(httpx.MockTransport(handler))

    with pytest.raises(GitHubRateLimitError):
        await client.get_repository("octocat/hello-world")


async def test_get_repository_plain_403_raises_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Forbidden"})

    client = _client(httpx.MockTransport(handler))

    with pytest.raises(GitHubResponseError):
        await client.get_repository("octocat/private-repo")


async def test_get_repository_5xx_raises_server_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"message": "Service Unavailable"})

    client = _client(httpx.MockTransport(handler))

    with pytest.raises(GitHubServerError):
        await client.get_repository("octocat/hello-world")


async def test_get_repository_timeout_raises_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    client = _client(httpx.MockTransport(handler))

    with pytest.raises(GitHubTimeoutError):
        await client.get_repository("octocat/hello-world")


async def test_get_repository_connection_failure_raises_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    client = _client(httpx.MockTransport(handler))

    with pytest.raises(GitHubConnectionError):
        await client.get_repository("octocat/hello-world")


async def test_get_repository_malformed_json_raises_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json at all {")

    client = _client(httpx.MockTransport(handler))

    with pytest.raises(GitHubResponseError):
        await client.get_repository("octocat/hello-world")


async def test_get_repository_missing_fields_raises_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"full_name": "octocat/hello-world"})

    client = _client(httpx.MockTransport(handler))

    with pytest.raises(GitHubResponseError):
        await client.get_repository("octocat/hello-world")
