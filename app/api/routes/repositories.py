from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.models.repository import Repository
from app.schemas.repository import RepositoryResponse

router = APIRouter()


@router.get("/repositories", response_model=list[RepositoryResponse])
async def list_repositories(db: Annotated[AsyncSession, Depends(get_db)]) -> list[Repository]:
    result = await db.execute(select(Repository).order_by(Repository.id))
    return list(result.scalars().all())
