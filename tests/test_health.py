from unittest.mock import patch

from fastapi.testclient import TestClient
from httpx import AsyncClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession


def test_health_returns_200(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200


def test_health_returns_expected_body(client: TestClient) -> None:
    response = client.get("/health")
    assert response.json() == {"status": "ok"}


def test_health_content_type_is_json(client: TestClient) -> None:
    response = client.get("/health")
    assert response.headers["content-type"] == "application/json"


def test_openapi_schema_is_available(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"


async def test_ready_returns_200_when_database_reachable(async_client: AsyncClient) -> None:
    response = await async_client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_returns_503_when_database_unreachable(
    async_client: AsyncClient, db_session: AsyncSession
) -> None:
    with patch.object(db_session, "execute", side_effect=SQLAlchemyError("boom")):
        response = await async_client.get("/ready")

    assert response.status_code == 503
    assert "boom" not in response.text
