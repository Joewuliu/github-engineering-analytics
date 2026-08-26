from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, get_github_client
from app.github.client import GitHubClient
from app.models.repository import Repository
from app.models.user import User
from app.models.user_repository import UserRepository
from app.schemas.repository import (
    RepositoryCreateRequest,
    RepositoryResponse,
    TrackedRepositoryResponse,
)
from app.services.repositories import (
    track_repository_for_user,
    untrack_repository_for_user,
)

router = APIRouter()


@router.get("/repositories", response_model=list[RepositoryResponse])
async def list_repositories(db: Annotated[AsyncSession, Depends(get_db)]) -> list[Repository]:
    result = await db.execute(select(Repository).order_by(Repository.id))
    return list(result.scalars().all())


@router.post(
    "/me/repositories",
    response_model=RepositoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def track_repository(
    payload: RepositoryCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    github_client: Annotated[GitHubClient, Depends(get_github_client)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Repository:
    return await track_repository_for_user(
        payload.full_name, user=current_user, db=db, github_client=github_client
    )


@router.get("/me/repositories", response_model=list[TrackedRepositoryResponse])
async def list_my_repositories(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[TrackedRepositoryResponse]:
    result = await db.execute(
        select(Repository, UserRepository.tracked_at)
        .join(UserRepository, UserRepository.repository_id == Repository.id)
        .where(UserRepository.user_id == current_user.id)
        .order_by(UserRepository.tracked_at.desc(), UserRepository.repository_id.desc())
    )
    return [
        TrackedRepositoryResponse(
            id=repository.id,
            github_id=repository.github_id,
            full_name=repository.full_name,
            created_at=repository.created_at,
            updated_at=repository.updated_at,
            tracked_at=tracked_at,
        )
        for repository, tracked_at in result.all()
    ]


@router.delete("/me/repositories/{repository_id}", status_code=status.HTTP_204_NO_CONTENT)
async def untrack_repository(
    repository_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    await untrack_repository_for_user(repository_id, user=current_user, db=db)
