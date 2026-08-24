from collections.abc import AsyncIterator

import httpx
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import async_session_factory
from app.github.client import GitHubClient


async def get_db() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


def get_github_client(request: Request) -> GitHubClient:
    http_client: httpx.AsyncClient = request.app.state.http_client
    settings = get_settings()
    return GitHubClient(http_client, token=settings.github_token)
