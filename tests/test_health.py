from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import AsyncClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.main import lifespan


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
    body = response.json()
    assert "openapi" in body
    assert "info" in body
    assert "paths" in body


def test_docs_available_in_development(client: TestClient) -> None:
    response = client.get("/docs")
    assert response.status_code == 200


def test_docs_and_openapi_available_under_production_settings() -> None:
    """/docs and /openapi.json must stay public under APP_ENV=production --
    app/main.py never makes docs_url/openapi_url conditional on app_env, so
    FastAPI's defaults ("/docs", "/openapi.json") apply regardless. Proves
    this directly by constructing the app exactly as app/main.py does (same
    FastAPI(...) call, same lifespan), just with production settings,
    rather than only asserting the (environment-independent) default on the
    module-level app singleton. Deliberately not entered as a `with`
    context manager -- that would run the real lifespan, which disposes the
    shared app.db.session.engine on exit and could affect other tests;
    /docs and /openapi.json don't depend on lifespan state anyway.
    """
    production_settings = Settings(app_env="production")
    production_app = FastAPI(title=production_settings.app_name, lifespan=lifespan)
    client = TestClient(production_app)

    docs_response = client.get("/docs")
    openapi_response = client.get("/openapi.json")

    assert docs_response.status_code == 200
    assert openapi_response.status_code == 200
    body = openapi_response.json()
    assert "openapi" in body
    assert "info" in body
    assert "paths" in body


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
