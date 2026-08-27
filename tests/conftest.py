import asyncio
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.api.deps import get_db, get_enqueue_sync_job, get_github_client, get_github_oauth_client
from app.config import get_settings
from app.core.security import hash_token
from app.db.base import Base
from app.db.session import engine
from app.github.client import GitHubClient
from app.github.oauth_client import GitHubOAuthClient
from app.main import app
from app.models import pull_request as pull_request_model  # noqa: F401  (registers table)
from app.models import pull_request_review as pull_request_review_model  # noqa: F401
from app.models import repository as repository_model  # noqa: F401  (registers table)
from app.models import session as session_model  # noqa: F401  (registers table)
from app.models import sync_job as sync_job_model  # noqa: F401  (registers table)
from app.models import user as user_model  # noqa: F401  (registers table)
from app.models import user_repository as user_repository_model  # noqa: F401  (registers table)
from app.models.session import Session
from app.models.user import User
from app.services.auth import SESSION_COOKIE_NAME

if sys.platform == "win32":
    # pytest-asyncio runs the async test suite in-process (unlike `uvicorn --reload`,
    # which happens to select a compatible loop for its own reasons — see README).
    # Without this, its default loop is ProactorEventLoop, which psycopg's async
    # driver cannot use.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

TEST_DATABASE_SUFFIX = "_test"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session", autouse=True)
def _verify_test_database() -> None:
    database_name = make_url(get_settings().database_url).database or ""
    if not database_name.endswith(TEST_DATABASE_SUFFIX):
        raise RuntimeError(
            "Refusing to run tests: DATABASE_URL does not point at a test database "
            f"(expected a name ending in {TEST_DATABASE_SUFFIX!r}, got {database_name!r}). "
            "Set DATABASE_URL to the test database before running pytest, e.g.:\n"
            '  DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/'
            'github_analytics_test"'
        )


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _create_schema(_verify_test_database: None) -> AsyncIterator[None]:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture
async def db_connection(_create_schema: None) -> AsyncIterator[AsyncConnection]:
    async with engine.connect() as connection:
        await connection.begin()
        yield connection
        await connection.rollback()


@pytest_asyncio.fixture
async def db_session(db_connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    session = AsyncSession(
        bind=db_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    async with session:
        yield session


@pytest.fixture
def db_session_factory(db_connection: AsyncConnection) -> Callable[[], AsyncSession]:
    """Builds a fresh AsyncSession per call, all bound to the same
    rollback-guarded test connection.

    Lets a test exercise code (like the background worker) that opens and
    closes several independent sessions in sequence -- matching real
    production shape -- while everything still participates in, and is
    undone by, the single outer test transaction.
    """

    def _factory() -> AsyncSession:
        return AsyncSession(
            bind=db_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )

    return _factory


@pytest_asyncio.fixture
async def async_client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_test_client:
        yield async_test_client
    app.dependency_overrides.clear()


@pytest.fixture
def stub_github_client() -> Iterator[Callable[[Callable[[httpx.Request], httpx.Response]], None]]:
    """Lets a test override the GitHub client with an httpx.MockTransport handler."""

    def _use(handler: Callable[[httpx.Request], httpx.Response]) -> None:
        mock_http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://api.github.com"
        )
        app.dependency_overrides[get_github_client] = lambda: GitHubClient(mock_http_client)

    yield _use
    app.dependency_overrides.pop(get_github_client, None)


@pytest.fixture
def stub_github_oauth_client() -> Iterator[
    Callable[[Callable[[httpx.Request], httpx.Response]], None]
]:
    """Lets a test override the GitHub OAuth client with an httpx.MockTransport handler."""

    def _use(handler: Callable[[httpx.Request], httpx.Response]) -> None:
        mock_http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        app.dependency_overrides[get_github_oauth_client] = lambda: GitHubOAuthClient(
            mock_http_client,
            client_id="test-client-id",
            client_secret="test-client-secret",
            callback_url="http://127.0.0.1:8000/auth/github/callback",
        )

    yield _use
    app.dependency_overrides.pop(get_github_oauth_client, None)


class FakeEnqueue:
    """Records job ids handed to enqueue_sync_job instead of touching Redis."""

    def __init__(self) -> None:
        self.enqueued: list[UUID] = []
        self.raise_error: Exception | None = None

    async def __call__(self, job_id: UUID) -> None:
        if self.raise_error is not None:
            raise self.raise_error
        self.enqueued.append(job_id)


@pytest.fixture
def stub_enqueue_sync_job() -> Iterator[FakeEnqueue]:
    """Overrides the sync route's enqueue dependency with an in-memory fake.

    No live Redis/Dramatiq broker is required for the ordinary test suite --
    this is the same dependency-injection seam stub_github_client already
    uses for the GitHub client.
    """
    fake = FakeEnqueue()
    app.dependency_overrides[get_enqueue_sync_job] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_enqueue_sync_job, None)


@pytest_asyncio.fixture
async def authenticated_client(
    async_client: AsyncClient, db_session: AsyncSession
) -> tuple[AsyncClient, User]:
    """Inserts a real User + Session row and attaches the session cookie."""
    user = User(github_id=900001, github_login="octocat")
    db_session.add(user)
    await db_session.flush()

    raw_token = "test-session-token"
    db_session.add(
        Session(
            token_hash=hash_token(raw_token),
            user_id=user.id,
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    )
    await db_session.commit()
    await db_session.refresh(user)

    async_client.cookies.set(SESSION_COOKIE_NAME, raw_token)
    return async_client, user
