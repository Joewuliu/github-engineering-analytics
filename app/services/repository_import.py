from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.github.client import GitHubClient
from app.models.repository import Repository


class RepositoryAlreadyExistsError(Exception):
    """Raised when the GitHub repository being imported is already tracked."""


async def import_repository(
    full_name: str, db: AsyncSession, github_client: GitHubClient
) -> Repository:
    github_repository = await github_client.get_repository(full_name)

    existing = await db.scalar(
        select(Repository).where(Repository.github_id == github_repository.id)
    )
    if existing is not None:
        raise RepositoryAlreadyExistsError

    repository = Repository(github_id=github_repository.id, full_name=github_repository.full_name)
    db.add(repository)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise RepositoryAlreadyExistsError from exc

    await db.refresh(repository)
    return repository
