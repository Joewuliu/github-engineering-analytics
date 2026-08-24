from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.repository import Repository


async def test_insert_and_query_repository(db_session: AsyncSession) -> None:
    db_session.add(Repository(github_id=1, full_name="octocat/hello-world"))
    await db_session.commit()

    result = await db_session.execute(select(Repository).where(Repository.github_id == 1))
    fetched = result.scalar_one()

    assert fetched.full_name == "octocat/hello-world"
    assert isinstance(fetched.created_at, datetime)
    assert isinstance(fetched.updated_at, datetime)


async def test_duplicate_github_id_raises_integrity_error(db_session: AsyncSession) -> None:
    db_session.add(Repository(github_id=2, full_name="octocat/repo-a"))
    await db_session.commit()

    db_session.add(Repository(github_id=2, full_name="octocat/repo-b"))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_duplicate_full_name_raises_integrity_error(db_session: AsyncSession) -> None:
    db_session.add(Repository(github_id=3, full_name="octocat/duplicate-name"))
    await db_session.commit()

    db_session.add(Repository(github_id=4, full_name="octocat/duplicate-name"))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_isolation_step_one_inserts_a_repository(db_session: AsyncSession) -> None:
    result = await db_session.execute(select(Repository))
    assert result.scalars().all() == []

    db_session.add(Repository(github_id=999999, full_name="isolation/test-repo"))
    await db_session.commit()


async def test_isolation_step_two_starts_with_an_empty_table(db_session: AsyncSession) -> None:
    # Depends on running after test_isolation_step_one_inserts_a_repository;
    # an empty result here is the proof that rollback-based isolation works.
    result = await db_session.execute(select(Repository))
    assert result.scalars().all() == []
