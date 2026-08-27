from collections.abc import AsyncIterator
from typing import Annotated

import httpx
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import async_session_factory
from app.github.client import GitHubClient
from app.github.oauth_client import GitHubOAuthClient
from app.models.user import User
from app.services.auth import (
    SESSION_COOKIE_NAME,
    GitHubOAuthNotConfiguredError,
    UnauthenticatedError,
    get_valid_session_user,
)
from app.services.sync_jobs import EnqueueSyncJob, enqueue_sync_job


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


def get_github_oauth_client(request: Request) -> GitHubOAuthClient:
    http_client: httpx.AsyncClient = request.app.state.http_client
    settings = get_settings()
    if not settings.github_oauth_client_id or not settings.github_oauth_client_secret:
        raise GitHubOAuthNotConfiguredError
    return GitHubOAuthClient(
        http_client,
        client_id=settings.github_oauth_client_id,
        client_secret=settings.github_oauth_client_secret,
        callback_url=settings.github_oauth_callback_url,
    )


async def get_current_user(request: Request, db: Annotated[AsyncSession, Depends(get_db)]) -> User:
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw_token:
        raise UnauthenticatedError
    return await get_valid_session_user(db, raw_token)


def get_enqueue_sync_job() -> EnqueueSyncJob:
    return enqueue_sync_job
