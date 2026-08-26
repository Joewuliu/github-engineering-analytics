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


def _pr_payload(id_: int, number: int, **overrides: object) -> dict[str, object]:
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


def _review_payload(id_: int, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": id_,
        "state": "APPROVED",
        "submitted_at": "2024-01-03T00:00:00Z",
        "user": {"login": "reviewer"},
    }
    payload.update(overrides)
    return payload


# ---- list_pull_requests ---------------------------------------------------------


async def test_list_pull_requests_returns_parsed_prs() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_pr_payload(1, 1), _pr_payload(2, 2)])

    client = _client(httpx.MockTransport(handler))

    prs = await client.list_pull_requests("octocat/hello-world", limit=25)

    assert [pr.number for pr in prs] == [1, 2]
    assert prs[0].author_login == "octocat"


async def test_list_pull_requests_requests_state_all_and_newest_first() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[])

    client = _client(httpx.MockTransport(handler))
    await client.list_pull_requests("octocat/hello-world", limit=25)

    query = captured[0].url.params
    assert query["state"] == "all"
    assert query["sort"] == "created"
    assert query["direction"] == "desc"


async def test_list_pull_requests_follows_multiple_pages() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("page") is None:
            return httpx.Response(
                200,
                json=[_pr_payload(1, 1), _pr_payload(2, 2)],
                headers={"Link": '<https://api.github.com/x?page=2>; rel="next"'},
            )
        return httpx.Response(200, json=[_pr_payload(3, 3)])

    client = _client(httpx.MockTransport(handler))

    prs = await client.list_pull_requests("octocat/hello-world", limit=25)

    assert [pr.number for pr in prs] == [1, 2, 3]


async def test_list_pull_requests_stops_at_configured_maximum() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        # Always claims there's a next page -- if the client kept following
        # it past the limit, this would loop / over-fetch.
        return httpx.Response(
            200,
            json=[_pr_payload(i, i) for i in range(1, 4)],  # 3 items
            headers={"Link": '<https://api.github.com/x?page=2>; rel="next"'},
        )

    client = _client(httpx.MockTransport(handler))

    prs = await client.list_pull_requests("octocat/hello-world", limit=2)

    assert [pr.number for pr in prs] == [1, 2]
    # No unnecessary extra page requested once the limit was reached.
    assert len(calls) == 1


async def test_list_pull_requests_5xx_raises_server_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"message": "Service Unavailable"})

    client = _client(httpx.MockTransport(handler))

    with pytest.raises(GitHubServerError):
        await client.list_pull_requests("octocat/hello-world", limit=25)


async def test_list_pull_requests_rate_limited_raises_rate_limit_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers={"X-RateLimit-Remaining": "0"}, json={})

    client = _client(httpx.MockTransport(handler))

    with pytest.raises(GitHubRateLimitError):
        await client.list_pull_requests("octocat/hello-world", limit=25)


async def test_list_pull_requests_malformed_response_raises_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": 1}])  # missing required fields

    client = _client(httpx.MockTransport(handler))

    with pytest.raises(GitHubResponseError):
        await client.list_pull_requests("octocat/hello-world", limit=25)


async def test_list_pull_requests_non_list_response_raises_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": "not a list"})

    client = _client(httpx.MockTransport(handler))

    with pytest.raises(GitHubResponseError):
        await client.list_pull_requests("octocat/hello-world", limit=25)


# ---- list_reviews -----------------------------------------------------------------


async def test_list_reviews_returns_parsed_reviews() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_review_payload(1), _review_payload(2)])

    client = _client(httpx.MockTransport(handler))

    reviews = await client.list_reviews("octocat/hello-world", 42)

    assert [r.id for r in reviews] == [1, 2]
    assert reviews[0].reviewer_login == "reviewer"


async def test_list_reviews_follows_multiple_pages_fully() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("page") is None:
            return httpx.Response(
                200,
                json=[_review_payload(1), _review_payload(2)],
                headers={"Link": '<https://api.github.com/x?page=2>; rel="next"'},
            )
        return httpx.Response(200, json=[_review_payload(3)])

    client = _client(httpx.MockTransport(handler))

    reviews = await client.list_reviews("octocat/hello-world", 42)

    assert [r.id for r in reviews] == [1, 2, 3]


async def test_list_reviews_malformed_response_raises_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"state": "APPROVED"}])  # missing id

    client = _client(httpx.MockTransport(handler))

    with pytest.raises(GitHubResponseError):
        await client.list_reviews("octocat/hello-world", 42)


async def test_list_reviews_null_user_yields_none_reviewer_login() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_review_payload(1, user=None)])

    client = _client(httpx.MockTransport(handler))

    reviews = await client.list_reviews("octocat/hello-world", 42)

    assert reviews[0].reviewer_login is None


async def test_list_pull_requests_null_user_yields_none_author_login() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_pr_payload(1, 1, user=None)])

    client = _client(httpx.MockTransport(handler))

    prs = await client.list_pull_requests("octocat/hello-world", limit=25)

    assert prs[0].author_login is None
