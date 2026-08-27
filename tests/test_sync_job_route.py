from datetime import UTC, datetime
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.repository import Repository
from app.models.sync_job import SyncJob
from app.models.user import User
from app.models.user_repository import UserRepository

AuthClient = tuple[AsyncClient, User]


async def _make_user(db_session: AsyncSession, github_id: int, login: str) -> User:
    user = User(github_id=github_id, github_login=login)
    db_session.add(user)
    await db_session.flush()
    return user


async def _make_tracked_repository(
    db_session: AsyncSession, user: User, github_id: int, full_name: str
) -> Repository:
    repository = Repository(github_id=github_id, full_name=full_name)
    db_session.add(repository)
    await db_session.flush()
    db_session.add(UserRepository(user_id=user.id, repository_id=repository.id))
    await db_session.commit()
    return repository


async def test_get_sync_job_requires_authentication(async_client: AsyncClient) -> None:
    response = await async_client.get(f"/me/sync-jobs/{uuid4()}")

    assert response.status_code == 401


async def test_get_own_queued_job_returns_200(
    authenticated_client: AuthClient, db_session: AsyncSession
) -> None:
    client, user = authenticated_client
    repository = await _make_tracked_repository(db_session, user, 701, "octocat/queued-job")
    job = SyncJob(user_id=user.id, repository_id=repository.id, status="queued")
    db_session.add(job)
    await db_session.commit()

    response = await client.get(f"/me/sync-jobs/{job.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == str(job.id)
    assert body["repository_id"] == repository.id
    assert body["status"] == "queued"
    assert body["pull_requests_processed"] is None
    assert body["reviews_processed"] is None
    assert body["started_at"] is None
    assert body["finished_at"] is None
    assert body["safe_error_code"] is None
    assert body["safe_error_message"] is None


async def test_get_own_succeeded_job_returns_full_fields(
    authenticated_client: AuthClient, db_session: AsyncSession
) -> None:
    client, user = authenticated_client
    repository = await _make_tracked_repository(db_session, user, 702, "octocat/succeeded-job")
    now = datetime.now(UTC)
    job = SyncJob(
        user_id=user.id,
        repository_id=repository.id,
        status="succeeded",
        pull_requests_processed=25,
        reviews_processed=40,
        started_at=now,
        finished_at=now,
    )
    db_session.add(job)
    await db_session.commit()

    response = await client.get(f"/me/sync-jobs/{job.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["pull_requests_processed"] == 25
    assert body["reviews_processed"] == 40
    assert body["started_at"] is not None
    assert body["finished_at"] is not None


async def test_get_own_failed_job_returns_safe_error_fields(
    authenticated_client: AuthClient, db_session: AsyncSession
) -> None:
    client, user = authenticated_client
    repository = await _make_tracked_repository(db_session, user, 703, "octocat/failed-job")
    job = SyncJob(
        user_id=user.id,
        repository_id=repository.id,
        status="failed",
        safe_error_code="github_rate_limited",
        safe_error_message="GitHub API rate limit exceeded.",
        finished_at=datetime.now(UTC),
    )
    db_session.add(job)
    await db_session.commit()

    response = await client.get(f"/me/sync-jobs/{job.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["safe_error_code"] == "github_rate_limited"
    assert body["safe_error_message"] == "GitHub API rate limit exceeded."


async def test_get_nonexistent_job_returns_404(authenticated_client: AuthClient) -> None:
    client, _user = authenticated_client

    response = await client.get(f"/me/sync-jobs/{uuid4()}")

    assert response.status_code == 404


async def test_get_another_users_job_returns_404(
    authenticated_client: AuthClient, db_session: AsyncSession
) -> None:
    client, _user = authenticated_client
    owner = await _make_user(db_session, 800101, "job-owner")
    repository = await _make_tracked_repository(db_session, owner, 704, "octocat/not-your-job")
    job = SyncJob(user_id=owner.id, repository_id=repository.id, status="queued")
    db_session.add(job)
    await db_session.commit()

    response = await client.get(f"/me/sync-jobs/{job.id}")

    assert response.status_code == 404
