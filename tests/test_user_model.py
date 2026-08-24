from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


async def test_insert_and_query_user(db_session: AsyncSession) -> None:
    db_session.add(User(github_id=1, github_login="octocat"))
    await db_session.commit()

    result = await db_session.execute(select(User).where(User.github_id == 1))
    fetched = result.scalar_one()

    assert fetched.github_login == "octocat"
    assert isinstance(fetched.created_at, datetime)
    assert isinstance(fetched.updated_at, datetime)


async def test_duplicate_github_id_raises_integrity_error(db_session: AsyncSession) -> None:
    db_session.add(User(github_id=2, github_login="user-a"))
    await db_session.commit()

    db_session.add(User(github_id=2, github_login="user-b"))
    with pytest.raises(IntegrityError):
        await db_session.commit()
