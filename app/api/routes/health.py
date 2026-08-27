from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.health import HealthResponse, ReadyResponse
from app.services.readiness import check_database_ready

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/ready", response_model=ReadyResponse)
async def readiness_check(db: Annotated[AsyncSession, Depends(get_db)]) -> ReadyResponse:
    await check_database_ready(db)
    return ReadyResponse(status="ok")
