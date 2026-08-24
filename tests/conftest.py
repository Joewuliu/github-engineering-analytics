import asyncio
import sys
from collections.abc import AsyncIterator, Callable, Iterator

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_github_client
from app.config import get_settings
from app.db.base import Base
from app.db.session import engine
from app.github.client import GitHubClient
from app.main import app
from app.models import repository as repository_model  # noqa: F401  (registers table)

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
async def db_session(_create_schema: None) -> AsyncIterator[AsyncSession]:
    async with engine.connect() as connection:
        await connection.begin()
        session = AsyncSession(bind=connection, join_transaction_mode="create_savepoint")
        async with session:
            yield session
        await connection.rollback()


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
