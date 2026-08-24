import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.api.errors import register_exception_handlers
from app.api.routes import auth, health, repositories
from app.config import get_settings
from app.core.logging import configure_logging
from app.db.session import engine

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("Starting %s (env=%s)", settings.app_name, settings.app_env)

    http_client = httpx.AsyncClient(
        base_url=settings.github_api_base_url, timeout=settings.github_request_timeout
    )
    app.state.http_client = http_client

    yield

    logger.info("Shutting down %s", settings.app_name)
    await http_client.aclose()
    await engine.dispose()


settings = get_settings()

app = FastAPI(title=settings.app_name, lifespan=lifespan)
register_exception_handlers(app)
app.include_router(health.router)
app.include_router(repositories.router)
app.include_router(auth.router)
