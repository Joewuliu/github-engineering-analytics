from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.repository import Repository


async def test_list_repositories_returns_empty_list(async_client: AsyncClient) -> None:
    response = await async_client.get("/repositories")
    assert response.status_code == 200
    assert response.json() == []


async def test_list_repositories_returns_inserted_repository(
    async_client: AsyncClient, db_session: AsyncSession
) -> None:
    db_session.add(Repository(github_id=42, full_name="octocat/answer"))
    await db_session.commit()

    response = await async_client.get("/repositories")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["github_id"] == 42
    assert body[0]["full_name"] == "octocat/answer"
