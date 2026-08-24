from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_github_client
from app.github.client import GitHubClient
from app.models.repository import Repository
from app.schemas.repository import RepositoryCreateRequest, RepositoryResponse
from app.services.repository_import import import_repository

router = APIRouter()


@router.get("/repositories", response_model=list[RepositoryResponse])
async def list_repositories(db: Annotated[AsyncSession, Depends(get_db)]) -> list[Repository]:
    result = await db.execute(select(Repository).order_by(Repository.id))
    return list(result.scalars().all())


@router.post(
    "/repositories",
    response_model=RepositoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_repository(
    payload: RepositoryCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    github_client: Annotated[GitHubClient, Depends(get_github_client)],
) -> Repository:
    return await import_repository(payload.full_name, db=db, github_client=github_client)
