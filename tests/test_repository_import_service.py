import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.github.client import GitHubClient
from app.models.repository import Repository
from app.services.repository_import import RepositoryAlreadyExistsError, import_repository


def _github_client(payload: dict[str, object]) -> GitHubClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.github.com"
    )
    return GitHubClient(http_client)


async def test_import_repository_persists_repository(db_session: AsyncSession) -> None:
    github_client = _github_client({"id": 111, "full_name": "octocat/hello-world"})

    repository = await import_repository("octocat/hello-world", db_session, github_client)

    assert repository.id is not None
    assert repository.github_id == 111
    assert repository.full_name == "octocat/hello-world"

    result = await db_session.execute(select(Repository).where(Repository.github_id == 111))
    assert result.scalar_one().full_name == "octocat/hello-world"


async def test_import_repository_stores_github_canonical_casing(db_session: AsyncSession) -> None:
    github_client = _github_client({"id": 222, "full_name": "Owner/MyRepo"})

    repository = await import_repository("owner/myrepo", db_session, github_client)

    assert repository.full_name == "Owner/MyRepo"


async def test_import_repository_duplicate_github_id_raises_conflict(
    db_session: AsyncSession,
) -> None:
    db_session.add(Repository(github_id=333, full_name="octocat/already-tracked"))
    await db_session.commit()

    github_client = _github_client({"id": 333, "full_name": "octocat/already-tracked"})

    with pytest.raises(RepositoryAlreadyExistsError):
        await import_repository("octocat/already-tracked", db_session, github_client)


async def test_import_repository_integrity_error_is_rolled_back_and_translated(
    db_session: AsyncSession,
) -> None:
    # Pre-insert a row with a *different* github_id but the same full_name. The
    # pre-check (keyed on github_id) won't find it, so the insert proceeds and
    # fails on the full_name unique constraint at commit time -- the same
    # failure shape a genuine concurrent-insert race would produce.
    db_session.add(Repository(github_id=444, full_name="octocat/collision"))
    await db_session.commit()

    github_client = _github_client({"id": 555, "full_name": "octocat/collision"})

    with pytest.raises(RepositoryAlreadyExistsError):
        await import_repository("octocat/collision", db_session, github_client)

    # The session must still be usable after the rollback.
    result = await db_session.execute(select(Repository).where(Repository.github_id == 555))
    assert result.scalar_one_or_none() is None
