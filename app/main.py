import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import health
from app.config import get_settings
from app.core.logging import configure_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("Starting %s (env=%s)", settings.app_name, settings.app_env)
    yield
    logger.info("Shutting down %s", settings.app_name)


settings = get_settings()

app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(health.router)
