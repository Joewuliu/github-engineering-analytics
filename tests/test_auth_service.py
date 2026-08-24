import base64
import hashlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_token
from app.github.oauth_client import GitHubOAuthClient
from app.models.session import Session
from app.models.user import User
from app.services.auth import (
    InvalidOAuthStateError,
    UnauthenticatedError,
    complete_github_login,
    delete_session,
    derive_pkce_code_challenge,
    generate_oauth_state,
    generate_pkce_code_verifier,
    get_valid_session_user,
    validate_oauth_state,
)


def _oauth_client(
    github_id: int, login: str, captured: list[httpx.Request] | None = None
) -> GitHubOAuthClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_fake_token"})
        return httpx.Response(200, json={"id": github_id, "login": login})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GitHubOAuthClient(
        http_client,
        client_id="test-client-id",
        client_secret="test-client-secret",
        callback_url="http://127.0.0.1:8000/auth/github/callback",
    )


def test_generate_oauth_state_returns_high_entropy_value() -> None:
    first = generate_oauth_state()
    second = generate_oauth_state()

    assert first != second
    assert len(first) >= 32


def test_validate_oauth_state_accepts_matching_values() -> None:
    validate_oauth_state("same-value", "same-value")  # must not raise


def test_validate_oauth_state_rejects_mismatch() -> None:
    with pytest.raises(InvalidOAuthStateError):
        validate_oauth_state("cookie-value", "different-value")


def test_validate_oauth_state_rejects_missing_cookie() -> None:
    with pytest.raises(InvalidOAuthStateError):
        validate_oauth_state(None, "query-value")


def test_validate_oauth_state_rejects_missing_query_param() -> None:
    with pytest.raises(InvalidOAuthStateError):
        validate_oauth_state("cookie-value", None)


# ---- PKCE -------------------------------------------------------------------


def test_generate_pkce_code_verifier_returns_high_entropy_value() -> None:
    first = generate_pkce_code_verifier()
    second = generate_pkce_code_verifier()

    assert first != second
    assert 43 <= len(first) <= 128


def test_derive_pkce_code_challenge_matches_rfc7636_s256() -> None:
    verifier = "a-fixed-test-verifier-value-for-deterministic-hashing"

    challenge = derive_pkce_code_challenge(verifier)

    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert challenge == expected
    assert "=" not in challenge


def test_derive_pkce_code_challenge_is_deterministic() -> None:
    verifier = generate_pkce_code_verifier()
    assert derive_pkce_code_challenge(verifier) == derive_pkce_code_challenge(verifier)


# ---- complete_github_login ---------------------------------------------------


async def test_complete_github_login_creates_new_user(db_session: AsyncSession) -> None:
    oauth_client = _oauth_client(github_id=555, login="octocat")

    user, raw_token, expires_at = await complete_github_login(
        "a-code", "a-verifier", db_session, oauth_client
    )

    assert user.github_id == 555
    assert user.github_login == "octocat"
    assert expires_at > datetime.now(UTC)

    result = await db_session.execute(select(User).where(User.github_id == 555))
    assert result.scalar_one().github_login == "octocat"


async def test_complete_github_login_sends_code_verifier_to_github(
    db_session: AsyncSession,
) -> None:
    captured: list[httpx.Request] = []
    oauth_client = _oauth_client(github_id=562, login="octocat", captured=captured)

    await complete_github_login("a-code", "the-exact-verifier", db_session, oauth_client)

    token_request = next(r for r in captured if r.url.path == "/login/oauth/access_token")
    body = token_request.read().decode("utf-8")
    assert "code_verifier=the-exact-verifier" in body


async def test_complete_github_login_reuses_existing_user(db_session: AsyncSession) -> None:
    db_session.add(User(github_id=556, github_login="octocat"))
    await db_session.commit()

    oauth_client = _oauth_client(github_id=556, login="octocat")
    user, _raw_token, _expires_at = await complete_github_login(
        "a-code", "a-verifier", db_session, oauth_client
    )

    result = await db_session.execute(select(User).where(User.github_id == 556))
    users = result.scalars().all()
    assert len(users) == 1
    assert user.id == users[0].id


async def test_complete_github_login_updates_changed_login(db_session: AsyncSession) -> None:
    db_session.add(User(github_id=557, github_login="old-login"))
    await db_session.commit()

    oauth_client = _oauth_client(github_id=557, login="new-login")
    user, _raw_token, _expires_at = await complete_github_login(
        "a-code", "a-verifier", db_session, oauth_client
    )

    assert user.github_login == "new-login"


async def test_complete_github_login_stores_only_token_hash(db_session: AsyncSession) -> None:
    oauth_client = _oauth_client(github_id=558, login="octocat")

    _user, raw_token, _expires_at = await complete_github_login(
        "a-code", "a-verifier", db_session, oauth_client
    )

    result = await db_session.execute(select(Session))
    session = result.scalar_one()

    assert session.token_hash == hash_token(raw_token)
    assert session.token_hash != raw_token
    assert raw_token not in session.token_hash


async def test_get_valid_session_user_returns_user_for_valid_token(
    db_session: AsyncSession,
) -> None:
    oauth_client = _oauth_client(github_id=559, login="octocat")
    user, raw_token, _expires_at = await complete_github_login(
        "a-code", "a-verifier", db_session, oauth_client
    )

    resolved = await get_valid_session_user(db_session, raw_token)

    assert resolved.id == user.id


async def test_get_valid_session_user_rejects_unknown_token(db_session: AsyncSession) -> None:
    with pytest.raises(UnauthenticatedError):
        await get_valid_session_user(db_session, "not-a-real-token")


async def test_get_valid_session_user_rejects_expired_session(db_session: AsyncSession) -> None:
    user = User(github_id=560, github_login="octocat")
    db_session.add(user)
    await db_session.flush()

    raw_token = "expired-token"
    db_session.add(
        Session(
            token_hash=hash_token(raw_token),
            user_id=user.id,
            expires_at=datetime.now(UTC) - timedelta(days=1),
        )
    )
    await db_session.commit()

    with pytest.raises(UnauthenticatedError):
        await get_valid_session_user(db_session, raw_token)


async def test_delete_session_removes_matching_row(db_session: AsyncSession) -> None:
    oauth_client = _oauth_client(github_id=561, login="octocat")
    _user, raw_token, _expires_at = await complete_github_login(
        "a-code", "a-verifier", db_session, oauth_client
    )

    await delete_session(db_session, raw_token)

    result = await db_session.execute(
        select(Session).where(Session.token_hash == hash_token(raw_token))
    )
    assert result.scalar_one_or_none() is None


async def test_delete_session_is_a_noop_for_unknown_token(db_session: AsyncSession) -> None:
    await delete_session(db_session, "never-existed")  # must not raise
