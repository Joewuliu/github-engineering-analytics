import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.repository import Repository
from app.models.sync_job import SyncJob
from app.models.user import User


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


async def test_sync_job_creation_populates_defaults(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, 1, "octocat")
    repository = await _make_repository(db_session, 1, "octocat/repo-a")

    job = SyncJob(user_id=user.id, repository_id=repository.id, status="queued")
    db_session.add(job)
    await db_session.commit()

    assert isinstance(job.id, uuid.UUID)
    assert job.status == "queued"
    assert job.pull_requests_processed is None
    assert job.reviews_processed is None
    assert job.created_at is not None
    assert job.started_at is None
    assert job.finished_at is None
    assert job.safe_error_code is None
    assert job.safe_error_message is None


async def test_sync_job_ids_are_unique_uuids(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, 2, "octocat")
    repository = await _make_repository(db_session, 2, "octocat/repo-b")

    first = SyncJob(user_id=user.id, repository_id=repository.id, status="succeeded")
    db_session.add(first)
    await db_session.flush()
    second = SyncJob(user_id=user.id, repository_id=repository.id, status="succeeded")
    db_session.add(second)
    await db_session.commit()

    assert first.id != second.id


async def test_sync_job_status_check_constraint_rejects_unknown_value(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session, 3, "octocat")
    repository = await _make_repository(db_session, 3, "octocat/repo-c")

    db_session.add(SyncJob(user_id=user.id, repository_id=repository.id, status="bogus"))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_sync_job_cascades_on_user_delete(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, 4, "octocat")
    repository = await _make_repository(db_session, 4, "octocat/repo-d")
    job = SyncJob(user_id=user.id, repository_id=repository.id, status="queued")
    db_session.add(job)
    await db_session.commit()
    job_id = job.id

    await db_session.delete(user)
    await db_session.commit()

    # populate_existing=True forces a real read past the identity map, which
    # would otherwise still hold the (now database-deleted) cached `job`.
    assert await db_session.get(SyncJob, job_id, populate_existing=True) is None


async def test_sync_job_cascades_on_repository_delete(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, 5, "octocat")
    repository = await _make_repository(db_session, 5, "octocat/repo-e")
    job = SyncJob(user_id=user.id, repository_id=repository.id, status="queued")
    db_session.add(job)
    await db_session.commit()
    job_id = job.id

    await db_session.delete(repository)
    await db_session.commit()

    assert await db_session.get(SyncJob, job_id, populate_existing=True) is None


async def test_sync_job_deleting_one_users_job_leaves_others_intact(
    db_session: AsyncSession,
) -> None:
    owner = await _make_user(db_session, 6, "owner")
    other = await _make_user(db_session, 7, "other")
    repository = await _make_repository(db_session, 6, "octocat/repo-f")
    owner_job = SyncJob(user_id=owner.id, repository_id=repository.id, status="succeeded")
    other_job = SyncJob(user_id=other.id, repository_id=repository.id, status="failed")
    db_session.add_all([owner_job, other_job])
    await db_session.commit()
    other_job_id = other_job.id

    await db_session.delete(owner)
    await db_session.commit()

    assert await db_session.get(SyncJob, other_job_id) is not None


async def test_sync_job_active_uniqueness_rejects_second_queued_job(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session, 8, "octocat")
    repository = await _make_repository(db_session, 7, "octocat/repo-g")
    db_session.add(SyncJob(user_id=user.id, repository_id=repository.id, status="queued"))
    await db_session.commit()

    db_session.add(SyncJob(user_id=user.id, repository_id=repository.id, status="running"))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_sync_job_active_uniqueness_allows_terminal_alongside_new_active(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session, 9, "octocat")
    repository = await _make_repository(db_session, 8, "octocat/repo-h")
    db_session.add(SyncJob(user_id=user.id, repository_id=repository.id, status="succeeded"))
    await db_session.commit()

    db_session.add(SyncJob(user_id=user.id, repository_id=repository.id, status="queued"))
    await db_session.commit()

    jobs = await db_session.execute(select(SyncJob).where(SyncJob.repository_id == repository.id))
    assert len(jobs.scalars().all()) == 2


async def test_sync_job_active_uniqueness_allows_two_terminal_jobs(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session, 10, "octocat")
    repository = await _make_repository(db_session, 9, "octocat/repo-i")
    db_session.add(SyncJob(user_id=user.id, repository_id=repository.id, status="succeeded"))
    await db_session.commit()

    db_session.add(SyncJob(user_id=user.id, repository_id=repository.id, status="failed"))
    await db_session.commit()

    jobs = await db_session.execute(select(SyncJob).where(SyncJob.repository_id == repository.id))
    assert len(jobs.scalars().all()) == 2
