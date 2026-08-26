from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, get_github_client
from app.github.client import GitHubClient
from app.models.repository import Repository
from app.models.user import User
from app.models.user_repository import UserRepository
from app.schemas.metrics import RepositoryMetricsResponse
from app.schemas.repository import (
    RepositoryCreateRequest,
    RepositoryResponse,
    TrackedRepositoryResponse,
)
from app.schemas.sync import RepositorySyncResponse
from app.services.repositories import (
    track_repository_for_user,
    untrack_repository_for_user,
)
from app.services.repository_metrics import get_repository_metrics
from app.services.repository_sync import sync_repository

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


@router.post("/me/repositories/{repository_id}/sync", response_model=RepositorySyncResponse)
async def sync_tracked_repository(
    repository_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    github_client: Annotated[GitHubClient, Depends(get_github_client)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> RepositorySyncResponse:
    repository, pr_count, review_count = await sync_repository(
        repository_id, user=current_user, db=db, github_client=github_client
    )
    return RepositorySyncResponse(
        repository_id=repository.id,
        full_name=repository.full_name,
        pull_requests_processed=pr_count,
        reviews_processed=review_count,
    )


@router.get("/me/repositories/{repository_id}/metrics", response_model=RepositoryMetricsResponse)
async def get_tracked_repository_metrics(
    repository_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> RepositoryMetricsResponse:
    result = await get_repository_metrics(repository_id, user=current_user, db=db)
    return RepositoryMetricsResponse(
        repository_id=result.repository.id,
        full_name=result.repository.full_name,
        total_pull_requests=result.total_pull_requests,
        merged_pull_requests=result.merged_pull_requests,
        merge_rate=_round2(result.merge_rate),
        median_pr_cycle_time_hours=_round2(result.median_pr_cycle_time_hours),
        median_time_to_first_review_hours=_round2(result.median_time_to_first_review_hours),
    )


def _round2(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None
