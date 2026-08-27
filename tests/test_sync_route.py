from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.repository import Repository
from app.models.sync_job import SyncJob
from app.models.user import User
from app.models.user_repository import UserRepository
from tests.conftest import FakeEnqueue

AuthClient = tuple[AsyncClient, User]


async def _make_user(db_session: AsyncSession, github_id: int, login: str) -> User:
    user = User(github_id=github_id, github_login=login)
    db_session.add(user)
    await db_session.flush()
    return user


async def _track(db_session: AsyncSession, user: User, repository: Repository) -> None:
    db_session.add(repository)
    await db_session.flush()
    db_session.add(UserRepository(user_id=user.id, repository_id=repository.id))
    await db_session.commit()


async def test_sync_requires_authentication(
    async_client: AsyncClient, stub_enqueue_sync_job: FakeEnqueue
) -> None:
    response = await async_client.post("/me/repositories/1/sync")

    assert response.status_code == 401


async def test_sync_nonexistent_repository_returns_404(
    authenticated_client: AuthClient, stub_enqueue_sync_job: FakeEnqueue
) -> None:
    client, _user = authenticated_client

    response = await client.post("/me/repositories/999999/sync")

    assert response.status_code == 404


async def test_sync_untracked_repository_returns_404(
    authenticated_client: AuthClient, stub_enqueue_sync_job: FakeEnqueue, db_session: AsyncSession
) -> None:
    client, _user = authenticated_client
    owner = await _make_user(db_session, 800001, "owner")
    repository = Repository(github_id=600, full_name="octocat/not-mine")
    await _track(db_session, owner, repository)

    response = await client.post(f"/me/repositories/{repository.id}/sync")

    assert response.status_code == 404


async def test_sync_tracked_repository_returns_202_with_queued_job(
    authenticated_client: AuthClient, stub_enqueue_sync_job: FakeEnqueue, db_session: AsyncSession
) -> None:
    client, user = authenticated_client
    repository = Repository(github_id=601, full_name="octocat/tracked-repo")
    await _track(db_session, user, repository)

    response = await client.post(f"/me/repositories/{repository.id}/sync")

    assert response.status_code == 202
    body = response.json()
    assert body["repository_id"] == repository.id
    assert body["status"] == "queued"
    job_id = UUID(body["job_id"])

    job = await db_session.get(SyncJob, job_id)
    assert job is not None
    assert job.status == "queued"
    assert job.user_id == user.id
    assert job.repository_id == repository.id


async def test_sync_enqueues_the_exact_job_id(
    authenticated_client: AuthClient, stub_enqueue_sync_job: FakeEnqueue, db_session: AsyncSession
) -> None:
    client, user = authenticated_client
    repository = Repository(github_id=602, full_name="octocat/enqueue-check")
    await _track(db_session, user, repository)

    response = await client.post(f"/me/repositories/{repository.id}/sync")

    job_id = UUID(response.json()["job_id"])
    assert stub_enqueue_sync_job.enqueued == [job_id]


async def test_sync_duplicate_active_job_returns_409_without_existing_job_id(
    authenticated_client: AuthClient, stub_enqueue_sync_job: FakeEnqueue, db_session: AsyncSession
) -> None:
    client, user = authenticated_client
    repository = Repository(github_id=603, full_name="octocat/already-syncing")
    await _track(db_session, user, repository)

    first = await client.post(f"/me/repositories/{repository.id}/sync")
    assert first.status_code == 202

    second = await client.post(f"/me/repositories/{repository.id}/sync")

    assert second.status_code == 409
    assert "job_id" not in second.json()
    # Only one job row exists -- the second attempt never created another.
    jobs = await db_session.execute(select(SyncJob).where(SyncJob.repository_id == repository.id))
    assert len(jobs.scalars().all()) == 1


async def test_sync_duplicate_active_job_from_another_user_also_returns_409(
    authenticated_client: AuthClient, stub_enqueue_sync_job: FakeEnqueue, db_session: AsyncSession
) -> None:
    """A canonical Repository can be tracked by multiple users; an active
    sync started by ANY of them blocks a new one for ANY of them, and the
    other user's job id is never revealed."""
    client, user = authenticated_client
    other = await _make_user(db_session, 800002, "other-tracker")
    repository = Repository(github_id=604, full_name="octocat/shared-repo")
    await _track(db_session, user, repository)
    db_session.add(UserRepository(user_id=other.id, repository_id=repository.id))
    await db_session.commit()

    db_session.add(SyncJob(user_id=other.id, repository_id=repository.id, status="running"))
    await db_session.commit()

    response = await client.post(f"/me/repositories/{repository.id}/sync")

    assert response.status_code == 409
    assert "job_id" not in response.json()


async def test_sync_enqueue_failure_marks_job_failed_and_returns_503(
    authenticated_client: AuthClient, stub_enqueue_sync_job: FakeEnqueue, db_session: AsyncSession
) -> None:
    client, user = authenticated_client
    repository = Repository(github_id=605, full_name="octocat/broker-down")
    await _track(db_session, user, repository)
    stub_enqueue_sync_job.raise_error = ConnectionError("redis unreachable")

    response = await client.post(f"/me/repositories/{repository.id}/sync")

    assert response.status_code == 503
    body = response.json()
    assert "redis unreachable" not in str(body)

    jobs = await db_session.execute(select(SyncJob).where(SyncJob.repository_id == repository.id))
    job = jobs.scalar_one()
    assert job.status == "failed"
    assert job.safe_error_code == "enqueue_failed"
    assert job.finished_at is not None


async def test_sync_after_enqueue_failure_can_be_retried(
    authenticated_client: AuthClient, stub_enqueue_sync_job: FakeEnqueue, db_session: AsyncSession
) -> None:
    """A failed-to-enqueue job is terminal, not active -- it must not block
    a subsequent retry with the same 409 duplicate-active-job guard."""
    client, user = authenticated_client
    repository = Repository(github_id=606, full_name="octocat/retry-after-enqueue-failure")
    await _track(db_session, user, repository)
    stub_enqueue_sync_job.raise_error = ConnectionError("redis unreachable")

    first = await client.post(f"/me/repositories/{repository.id}/sync")
    assert first.status_code == 503

    stub_enqueue_sync_job.raise_error = None
    second = await client.post(f"/me/repositories/{repository.id}/sync")

    assert second.status_code == 202
