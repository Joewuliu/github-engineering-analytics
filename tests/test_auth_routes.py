import base64
import hashlib
from collections.abc import Callable
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.session import Session
from app.models.user import User

StubOAuth = Callable[[Callable[[httpx.Request], httpx.Response]], None]

FAKE_ACCESS_TOKEN = "gho_fake_access_token_should_never_leak"
CLIENT_SECRET = "test-client-secret"
PKCE_VERIFIER = "a-fixed-pkce-verifier-value-for-tests-1234567890"


def _success_handler(
    github_id: int = 42, login: str = "octocat"
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": FAKE_ACCESS_TOKEN})
        return httpx.Response(200, json={"id": github_id, "login": login})

    return handler


def _set_oauth_state_cookie(client: AsyncClient, state: str) -> None:
    client.cookies.set("oauth_state", state)


def _set_pkce_cookie(client: AsyncClient, verifier: str = PKCE_VERIFIER) -> None:
    client.cookies.set("pkce_verifier", verifier)


# ---- login ----------------------------------------------------------------


async def test_github_login_redirects_to_github(
    async_client: AsyncClient, stub_github_oauth_client: StubOAuth
) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    stub_github_oauth_client(handler)

    response = await async_client.get("/auth/github/login")

    assert response.status_code == 302
    # Building the authorize URL is pure string construction -- no GitHub call.
    assert calls == []

    location = response.headers["location"]
    parsed = urlparse(location)
    assert parsed.scheme == "https"
    assert parsed.netloc == "github.com"
    assert parsed.path == "/login/oauth/authorize"

    query = parse_qs(parsed.query)
    assert query["client_id"] == ["test-client-id"]
    assert query["redirect_uri"] == ["http://127.0.0.1:8000/auth/github/callback"]
    assert "state" in query
    assert len(query["state"][0]) >= 32
    assert "code_challenge" in query
    assert query["code_challenge_method"] == ["S256"]


async def test_github_login_sets_oauth_state_cookie(
    async_client: AsyncClient, stub_github_oauth_client: StubOAuth
) -> None:
    stub_github_oauth_client(lambda request: httpx.Response(200))

    response = await async_client.get("/auth/github/login")

    set_cookie_headers = response.headers.get_list("set-cookie")
    state_cookie = next(h for h in set_cookie_headers if h.startswith("oauth_state="))
    assert "HttpOnly" in state_cookie
    assert "samesite=lax" in state_cookie.lower()


async def test_github_login_sets_pkce_verifier_cookie(
    async_client: AsyncClient, stub_github_oauth_client: StubOAuth
) -> None:
    stub_github_oauth_client(lambda request: httpx.Response(200))

    response = await async_client.get("/auth/github/login")

    set_cookie_headers = response.headers.get_list("set-cookie")
    pkce_cookie = next(h for h in set_cookie_headers if h.startswith("pkce_verifier="))
    assert "HttpOnly" in pkce_cookie
    assert "samesite=lax" in pkce_cookie.lower()


async def test_github_login_cookies_not_secure_in_development(
    async_client: AsyncClient, stub_github_oauth_client: StubOAuth
) -> None:
    """app_env defaults to "development" -- confirms Secure is opt-in, not
    accidentally always-on, before the paired production test below proves
    the opposite case."""
    stub_github_oauth_client(lambda request: httpx.Response(200))

    response = await async_client.get("/auth/github/login")

    set_cookie_headers = response.headers.get_list("set-cookie")
    assert all("secure" not in h.lower() for h in set_cookie_headers)


async def test_github_login_cookies_are_secure_in_production(
    async_client: AsyncClient,
    stub_github_oauth_client: StubOAuth,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.api.routes.auth.get_settings", lambda: Settings(app_env="production"))
    stub_github_oauth_client(lambda request: httpx.Response(200))

    response = await async_client.get("/auth/github/login")

    set_cookie_headers = response.headers.get_list("set-cookie")
    state_cookie = next(h for h in set_cookie_headers if h.startswith("oauth_state="))
    pkce_cookie = next(h for h in set_cookie_headers if h.startswith("pkce_verifier="))
    assert "secure" in state_cookie.lower()
    assert "secure" in pkce_cookie.lower()
    assert "HttpOnly" in state_cookie
    assert "samesite=lax" in state_cookie.lower()


async def test_github_login_code_challenge_matches_verifier_cookie(
    async_client: AsyncClient, stub_github_oauth_client: StubOAuth
) -> None:
    stub_github_oauth_client(lambda request: httpx.Response(200))

    response = await async_client.get("/auth/github/login")

    query = parse_qs(urlparse(response.headers["location"]).query)
    sent_challenge = query["code_challenge"][0]

    cookie_verifier = response.cookies["pkce_verifier"]
    expected_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(cookie_verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert sent_challenge == expected_challenge


# ---- callback ---------------------------------------------------------------


async def test_callback_valid_flow_creates_session_and_redirects(
    async_client: AsyncClient, stub_github_oauth_client: StubOAuth, db_session: AsyncSession
) -> None:
    stub_github_oauth_client(_success_handler(github_id=100, login="octocat"))
    _set_oauth_state_cookie(async_client, "matching-state")
    _set_pkce_cookie(async_client)

    response = await async_client.get(
        "/auth/github/callback", params={"code": "a-code", "state": "matching-state"}
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/auth/me"

    set_cookie_headers = response.headers.get_list("set-cookie")
    session_cookie = next(h for h in set_cookie_headers if h.startswith("session="))
    assert "HttpOnly" in session_cookie
    assert "samesite=lax" in session_cookie.lower()

    # Both single-use flow cookies are cleared after a successful callback.
    state_clear = next(h for h in set_cookie_headers if h.startswith("oauth_state="))
    pkce_clear = next(h for h in set_cookie_headers if h.startswith("pkce_verifier="))
    assert "Max-Age=0" in state_clear
    assert "Max-Age=0" in pkce_clear

    result = await db_session.execute(select(User).where(User.github_id == 100))
    assert result.scalar_one().github_login == "octocat"

    session_result = await db_session.execute(select(Session))
    assert session_result.scalar_one() is not None

    assert FAKE_ACCESS_TOKEN not in response.text
    assert CLIENT_SECRET not in response.text
    assert PKCE_VERIFIER not in response.text


async def test_callback_session_cookie_is_secure_in_production(
    async_client: AsyncClient,
    stub_github_oauth_client: StubOAuth,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.api.routes.auth.get_settings", lambda: Settings(app_env="production"))
    stub_github_oauth_client(_success_handler(github_id=101, login="octocat"))
    _set_oauth_state_cookie(async_client, "matching-state")
    _set_pkce_cookie(async_client)

    response = await async_client.get(
        "/auth/github/callback", params={"code": "a-code", "state": "matching-state"}
    )

    set_cookie_headers = response.headers.get_list("set-cookie")
    session_cookie = next(h for h in set_cookie_headers if h.startswith("session="))
    assert "secure" in session_cookie.lower()
    assert "HttpOnly" in session_cookie
    assert "samesite=lax" in session_cookie.lower()


async def test_callback_missing_state_returns_400_without_calling_github(
    async_client: AsyncClient, stub_github_oauth_client: StubOAuth
) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"access_token": FAKE_ACCESS_TOKEN})

    stub_github_oauth_client(handler)
    # No oauth_state cookie set.

    response = await async_client.get(
        "/auth/github/callback", params={"code": "a-code", "state": "some-state"}
    )

    assert response.status_code == 400
    assert calls == []


async def test_callback_mismatched_state_returns_400_without_calling_github(
    async_client: AsyncClient, stub_github_oauth_client: StubOAuth
) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"access_token": FAKE_ACCESS_TOKEN})

    stub_github_oauth_client(handler)
    _set_oauth_state_cookie(async_client, "cookie-state")
    _set_pkce_cookie(async_client)

    response = await async_client.get(
        "/auth/github/callback", params={"code": "a-code", "state": "different-state"}
    )

    assert response.status_code == 400
    assert calls == []

    set_cookie_headers = response.headers.get_list("set-cookie")
    assert any(h.startswith("oauth_state=") and "Max-Age=0" in h for h in set_cookie_headers)
    assert any(h.startswith("pkce_verifier=") and "Max-Age=0" in h for h in set_cookie_headers)


async def test_callback_missing_pkce_verifier_returns_400_without_calling_github(
    async_client: AsyncClient, stub_github_oauth_client: StubOAuth
) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"access_token": FAKE_ACCESS_TOKEN})

    stub_github_oauth_client(handler)
    _set_oauth_state_cookie(async_client, "matching-state")
    # No pkce_verifier cookie set.

    response = await async_client.get(
        "/auth/github/callback", params={"code": "a-code", "state": "matching-state"}
    )

    assert response.status_code == 400
    assert calls == []


async def test_callback_missing_code_returns_400(
    async_client: AsyncClient, stub_github_oauth_client: StubOAuth
) -> None:
    stub_github_oauth_client(_success_handler())
    _set_oauth_state_cookie(async_client, "matching-state")
    _set_pkce_cookie(async_client)

    response = await async_client.get("/auth/github/callback", params={"state": "matching-state"})

    assert response.status_code == 400


async def test_callback_authorization_denied_returns_safe_response(
    async_client: AsyncClient, stub_github_oauth_client: StubOAuth
) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"access_token": FAKE_ACCESS_TOKEN})

    stub_github_oauth_client(handler)
    _set_oauth_state_cookie(async_client, "matching-state")
    _set_pkce_cookie(async_client)

    response = await async_client.get(
        "/auth/github/callback",
        params={"error": "access_denied", "state": "matching-state"},
    )

    assert response.status_code == 200
    assert response.json() == {"detail": "GitHub authorization was cancelled."}
    assert calls == []

    set_cookie_headers = response.headers.get_list("set-cookie")
    assert any(h.startswith("oauth_state=") and "Max-Age=0" in h for h in set_cookie_headers)
    assert any(h.startswith("pkce_verifier=") and "Max-Age=0" in h for h in set_cookie_headers)


async def test_callback_token_exchange_failure_returns_mapped_status(
    async_client: AsyncClient, stub_github_oauth_client: StubOAuth
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    stub_github_oauth_client(handler)
    _set_oauth_state_cookie(async_client, "matching-state")
    _set_pkce_cookie(async_client)

    response = await async_client.get(
        "/auth/github/callback", params={"code": "a-code", "state": "matching-state"}
    )

    assert response.status_code == 502
    assert CLIENT_SECRET not in response.text
    assert PKCE_VERIFIER not in response.text


async def test_callback_user_lookup_failure_returns_mapped_status(
    async_client: AsyncClient, stub_github_oauth_client: StubOAuth
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": FAKE_ACCESS_TOKEN})
        return httpx.Response(401, json={"message": "Bad credentials"})

    stub_github_oauth_client(handler)
    _set_oauth_state_cookie(async_client, "matching-state")
    _set_pkce_cookie(async_client)

    response = await async_client.get(
        "/auth/github/callback", params={"code": "a-code", "state": "matching-state"}
    )

    assert response.status_code == 502
    assert FAKE_ACCESS_TOKEN not in response.text
    assert PKCE_VERIFIER not in response.text


# ---- /auth/me ---------------------------------------------------------------


async def test_me_authenticated_returns_current_user(
    authenticated_client: tuple[AsyncClient, User],
) -> None:
    client, user = authenticated_client

    response = await client.get("/auth/me")

    assert response.status_code == 200
    body = response.json()
    assert body["github_id"] == user.github_id
    assert body["github_login"] == user.github_login


async def test_me_unauthenticated_returns_401(async_client: AsyncClient) -> None:
    response = await async_client.get("/auth/me")
    assert response.status_code == 401


async def test_me_unknown_session_returns_401(async_client: AsyncClient) -> None:
    async_client.cookies.set("session", "not-a-real-token")
    response = await async_client.get("/auth/me")
    assert response.status_code == 401


# ---- logout -------------------------------------------------------------------


async def test_logout_deletes_session_and_clears_cookie(
    authenticated_client: tuple[AsyncClient, User], db_session: AsyncSession
) -> None:
    client, _user = authenticated_client

    response = await client.post("/auth/logout")

    assert response.status_code == 204
    set_cookie_headers = response.headers.get_list("set-cookie")
    session_cookie = next(h for h in set_cookie_headers if h.startswith("session="))
    cleared = 'session=""' in session_cookie or "session=;" in session_cookie
    assert cleared or "Max-Age=0" in session_cookie

    result = await db_session.execute(select(Session))
    assert result.scalar_one_or_none() is None

    me_response = await client.get("/auth/me")
    assert me_response.status_code == 401


async def test_logout_succeeds_when_already_logged_out(async_client: AsyncClient) -> None:
    response = await async_client.post("/auth/logout")
    assert response.status_code == 204
