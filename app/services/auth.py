import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.security import hash_token
from app.github.oauth_client import GitHubOAuthClient
from app.models.session import Session
from app.models.user import User

SESSION_COOKIE_NAME = "session"
OAUTH_STATE_COOKIE_NAME = "oauth_state"
PKCE_COOKIE_NAME = "pkce_verifier"


class InvalidOAuthStateError(Exception):
    """Raised when the OAuth state parameter is missing or does not match."""


class MissingAuthorizationCodeError(Exception):
    """Raised when the OAuth callback has neither a code nor an error param."""


class MissingPkceVerifierError(Exception):
    """Raised when the OAuth callback is missing its PKCE verifier cookie."""


class GitHubOAuthNotConfiguredError(Exception):
    """Raised when GitHub OAuth client credentials are not configured."""


class UnauthenticatedError(Exception):
    """Raised when there is no valid authenticated session."""


def generate_oauth_state() -> str:
    return secrets.token_urlsafe(32)


def validate_oauth_state(cookie_state: str | None, query_state: str | None) -> None:
    if not cookie_state or not query_state:
        raise InvalidOAuthStateError
    if not secrets.compare_digest(cookie_state, query_state):
        raise InvalidOAuthStateError


def generate_pkce_code_verifier() -> str:
    """A high-entropy verifier using RFC 7636's unreserved character set."""
    return secrets.token_urlsafe(32)


def derive_pkce_code_challenge(code_verifier: str) -> str:
    """S256: base64url(sha256(code_verifier)), no padding, per RFC 7636."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


async def complete_github_login(
    code: str, code_verifier: str, db: AsyncSession, oauth_client: GitHubOAuthClient
) -> tuple[User, str, datetime]:
    """Exchange the code, identify the user, upsert User, create a Session.

    Returns (user, raw_session_token, session_expires_at). The raw token is
    only ever returned to the caller to set the cookie -- it is never stored.
    """
    access_token = await oauth_client.exchange_code_for_token(code, code_verifier)
    github_user = await oauth_client.get_authenticated_user(access_token)
    # access_token is discarded here -- never persisted or logged.

    user = await db.scalar(select(User).where(User.github_id == github_user.id))
    if user is None:
        user = User(github_id=github_user.id, github_login=github_user.login)
        db.add(user)
    elif user.github_login != github_user.login:
        user.github_login = github_user.login

    await db.flush()

    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(seconds=get_settings().session_max_age_seconds)
    db.add(Session(token_hash=hash_token(raw_token), user_id=user.id, expires_at=expires_at))

    await db.commit()
    await db.refresh(user)

    return user, raw_token, expires_at


async def get_valid_session_user(db: AsyncSession, raw_token: str) -> User:
    session = await db.scalar(select(Session).where(Session.token_hash == hash_token(raw_token)))
    if session is None or session.expires_at < datetime.now(UTC):
        raise UnauthenticatedError

    user = await db.get(User, session.user_id)
    if user is None:
        raise UnauthenticatedError
    return user


async def delete_session(db: AsyncSession, raw_token: str) -> None:
    session = await db.scalar(select(Session).where(Session.token_hash == hash_token(raw_token)))
    if session is not None:
        await db.delete(session)
        await db.commit()
