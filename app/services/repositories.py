from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.github.client import GitHubClient
from app.models.repository import Repository
from app.models.user import User
from app.models.user_repository import UserRepository


class RepositoryAlreadyTrackedError(Exception):
    """Raised when the current user already tracks the requested repository."""


async def resolve_repository(
    full_name: str, db: AsyncSession, github_client: GitHubClient
) -> Repository:
    """Return the canonical Repository for full_name, creating it if needed.

    Handles the concurrent-creation race transparently: if another request's
    insert wins first, this re-selects the row it created (by github_id, or
    by full_name as a fallback for the rarer case of a stale local row left
    behind by a GitHub-side rename) instead of surfacing the IntegrityError.
    """
    github_repository = await github_client.get_repository(full_name)

    existing = await db.scalar(
        select(Repository).where(Repository.github_id == github_repository.id)
    )
    if existing is not None:
        return existing

    repository = Repository(github_id=github_repository.id, full_name=github_repository.full_name)
    db.add(repository)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        recovered = await db.scalar(
            select(Repository).where(
                (Repository.github_id == github_repository.id)
                | (Repository.full_name == github_repository.full_name)
            )
        )
        if recovered is None:
            raise
        return recovered

    return repository


async def track_repository_for_user(
    full_name: str, user: User, db: AsyncSession, github_client: GitHubClient
) -> Repository:
    repository = await resolve_repository(full_name, db, github_client)

    existing = await db.scalar(
        select(UserRepository).where(
            UserRepository.user_id == user.id,
            UserRepository.repository_id == repository.id,
        )
    )
    if existing is not None:
        raise RepositoryAlreadyTrackedError

    db.add(UserRepository(user_id=user.id, repository_id=repository.id))
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise RepositoryAlreadyTrackedError from exc

    await db.refresh(repository)
    return repository


async def untrack_repository_for_user(repository_id: int, user: User, db: AsyncSession) -> None:
    existing = await db.scalar(
        select(UserRepository).where(
            UserRepository.user_id == user.id,
            UserRepository.repository_id == repository_id,
        )
    )
    if existing is not None:
        await db.delete(existing)
        await db.commit()
