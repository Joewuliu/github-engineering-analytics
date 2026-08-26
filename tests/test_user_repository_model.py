from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.repository import Repository
from app.models.user import User
from app.models.user_repository import UserRepository


async def _make_user(db_session: AsyncSession, github_id: int, login: str) -> User:
    user = User(github_id=github_id, github_login=login)
    db_session.add(user)
    await db_session.flush()
    return user


async def _make_repository(db_session: AsyncSession, github_id: int, full_name: str) -> Repository:
    repository = Repository(github_id=github_id, full_name=full_name)
    db_session.add(repository)
    await db_session.flush()
    return repository


async def test_user_can_track_repository(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, 1, "octocat")
    repository = await _make_repository(db_session, 100, "octocat/hello-world")

    db_session.add(UserRepository(user_id=user.id, repository_id=repository.id))
    await db_session.commit()

    result = await db_session.execute(
        select(UserRepository).where(
            UserRepository.user_id == user.id, UserRepository.repository_id == repository.id
        )
    )
    association = result.scalar_one()
    assert isinstance(association.tracked_at, datetime)


async def test_same_user_cannot_track_same_repository_twice(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, 2, "octocat")
    repository = await _make_repository(db_session, 101, "octocat/repo-a")

    db_session.add(UserRepository(user_id=user.id, repository_id=repository.id))
    await db_session.commit()

    db_session.add(UserRepository(user_id=user.id, repository_id=repository.id))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_different_users_can_track_the_same_repository(db_session: AsyncSession) -> None:
    user_a = await _make_user(db_session, 3, "user-a")
    user_b = await _make_user(db_session, 4, "user-b")
    repository = await _make_repository(db_session, 102, "octocat/shared-repo")

    db_session.add(UserRepository(user_id=user_a.id, repository_id=repository.id))
    db_session.add(UserRepository(user_id=user_b.id, repository_id=repository.id))
    await db_session.commit()

    result = await db_session.execute(
        select(UserRepository).where(UserRepository.repository_id == repository.id)
    )
    trackers = result.scalars().all()
    assert {t.user_id for t in trackers} == {user_a.id, user_b.id}


async def test_deleting_association_leaves_repository_intact(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, 5, "octocat")
    repository = await _make_repository(db_session, 103, "octocat/repo-b")

    association = UserRepository(user_id=user.id, repository_id=repository.id)
    db_session.add(association)
    await db_session.commit()

    await db_session.delete(association)
    await db_session.commit()

    result = await db_session.execute(select(Repository).where(Repository.id == repository.id))
    assert result.scalar_one() is not None

    remaining = await db_session.execute(
        select(UserRepository).where(UserRepository.repository_id == repository.id)
    )
    assert remaining.scalar_one_or_none() is None


async def test_deleting_user_cascades_their_associations_only(db_session: AsyncSession) -> None:
    user_a = await _make_user(db_session, 6, "user-a")
    user_b = await _make_user(db_session, 7, "user-b")
    repository = await _make_repository(db_session, 104, "octocat/repo-c")

    db_session.add(UserRepository(user_id=user_a.id, repository_id=repository.id))
    db_session.add(UserRepository(user_id=user_b.id, repository_id=repository.id))
    await db_session.commit()

    await db_session.delete(user_a)
    await db_session.commit()

    result = await db_session.execute(
        select(UserRepository).where(UserRepository.repository_id == repository.id)
    )
    remaining = result.scalars().all()
    assert [a.user_id for a in remaining] == [user_b.id]

    # The canonical Repository is untouched by a User deletion.
    repo_result = await db_session.execute(select(Repository).where(Repository.id == repository.id))
    assert repo_result.scalar_one() is not None
