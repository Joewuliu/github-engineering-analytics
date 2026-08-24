import contextlib

from sqlalchemy import text

from app.api.deps import get_db
from app.db.session import engine


async def test_database_connectivity() -> None:
    async with engine.connect() as connection:
        result = await connection.execute(text("SELECT 1"))
        assert result.scalar_one() == 1


async def test_get_db_yields_working_session() -> None:
    async with contextlib.aclosing(get_db()) as gen:
        session = await anext(gen)
        result = await session.execute(text("SELECT 1"))
        assert result.scalar_one() == 1
