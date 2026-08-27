from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession


class DatabaseNotReadyError(Exception):
    """Raised when PostgreSQL cannot be reached for a readiness check."""


async def check_database_ready(db: AsyncSession) -> None:
    """Prove the database is reachable with the cheapest possible query.

    Deliberately checks PostgreSQL only -- not GitHub (external, expected to
    be occasionally unavailable, must never gate our own readiness) and not
    Redis (only needed for creating a sync job, not for reading repositories
    or metrics, so it shouldn't make the whole API report unready).
    """
    try:
        await db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise DatabaseNotReadyError from exc
