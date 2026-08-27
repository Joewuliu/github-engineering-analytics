from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID

import anyio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sync_job import SyncJob
from app.models.user import User
from app.services.repositories import get_tracked_repository
from app.worker.tasks import sync_repository_actor

EnqueueSyncJob = Callable[[UUID], Awaitable[None]]

_ACTIVE_STATUSES = ("queued", "running")


class RepositorySyncAlreadyActiveError(Exception):
    """A queued or running SyncJob already exists for this repository."""


class SyncJobEnqueueError(Exception):
    """The SyncJob was created but could not be handed off to the worker."""


class SyncJobNotFoundError(Exception):
    """No SyncJob with this id is visible to the current user."""


async def enqueue_sync_job(job_id: UUID) -> None:
    """Hand a job id to Dramatiq/Redis without blocking the event loop.

    Dramatiq's RedisBroker.send() performs synchronous Redis I/O; running it
    in a worker thread keeps the FastAPI asyncio event loop free. Only the
    job id (a string) crosses the queue boundary -- never an ORM object, a
    session, or any credential.
    """
    await anyio.to_thread.run_sync(sync_repository_actor.send, str(job_id))


async def create_sync_job(
    repository_id: int, user: User, db: AsyncSession, enqueue: EnqueueSyncJob
) -> SyncJob:
    """Authorize, durably create, and enqueue a background sync job.

    Reuses get_tracked_repository for identical 401/404 semantics to every
    other repository-scoped endpoint. Raises RepositorySyncAlreadyActiveError
    if a queued/running job already exists for the canonical repository --
    regardless of which user owns it, since Repository is a single global
    catalog row and two overlapping syncs would race on the same GitHub
    data. If the job commits but enqueueing fails, the job is marked
    failed (not left as a queued job nothing will ever consume) and
    SyncJobEnqueueError is raised.
    """
    repository = await get_tracked_repository(repository_id, user, db)

    existing_active = await db.scalar(
        select(SyncJob).where(
            SyncJob.repository_id == repository.id,
            SyncJob.status.in_(_ACTIVE_STATUSES),
        )
    )
    if existing_active is not None:
        raise RepositorySyncAlreadyActiveError

    job = SyncJob(user_id=user.id, repository_id=repository.id, status="queued")
    db.add(job)
    try:
        await db.commit()
    except IntegrityError:
        # The partial unique index rejected a concurrent duplicate that
        # slipped past the pre-check above -- same safe, generic response
        # as the pre-check catching it, no SQL details leaked.
        await db.rollback()
        raise RepositorySyncAlreadyActiveError from None

    try:
        await enqueue(job.id)
    except Exception as exc:
        job.status = "failed"
        job.safe_error_code = "enqueue_failed"
        job.safe_error_message = "Could not schedule the sync job. Try again later."
        job.finished_at = datetime.now(UTC)
        await db.commit()
        raise SyncJobEnqueueError from exc

    return job


async def get_own_sync_job(job_id: UUID, user: User, db: AsyncSession) -> SyncJob:
    """Return job_id's SyncJob, if it belongs to the current user.

    Raises SyncJobNotFoundError uniformly for a nonexistent job and one
    owned by another user -- the same never-reveal-existence pattern
    get_tracked_repository already uses for repositories.
    """
    job = await db.get(SyncJob, job_id)
    if job is None or job.user_id != user.id:
        raise SyncJobNotFoundError
    return job
