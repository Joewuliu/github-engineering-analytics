import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

import dramatiq
import httpx
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import async_session_factory
from app.github.client import GitHubClient
from app.github.errors import (
    GitHubAuthenticationError,
    GitHubConnectionError,
    GitHubRateLimitError,
    GitHubRepositoryNotFoundError,
    GitHubResponseError,
    GitHubServerError,
    GitHubTimeoutError,
)
from app.models import pull_request as pull_request_model  # noqa: F401  (registers table)
from app.models import pull_request_review as pull_request_review_model  # noqa: F401
from app.models import session as session_model  # noqa: F401  (registers table)
from app.models import user as user_model  # noqa: F401  (registers table)
from app.models import user_repository as user_repository_model  # noqa: F401
from app.models.repository import Repository
from app.models.sync_job import SyncJob
from app.services.repository_sync import sync_repository
from app.worker.broker import broker  # noqa: F401  (registers the global Dramatiq broker)

# The imports above (besides Repository/SyncJob, which this module uses
# directly) exist purely so every mapped class is registered on the shared
# declarative registry before any query runs -- Repository.trackers and
# similar relationships resolve their target ("UserRepository", etc.) by
# string lookup against whatever has actually been imported *in this
# process*, and the worker's own import graph is otherwise narrower than
# the FastAPI app's (which pulls all of these in transitively via its
# route modules). Same reasoning as tests/conftest.py and alembic/env.py.

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AsyncSession]
HttpClientFactory = Callable[[], httpx.AsyncClient]


def _default_http_client_factory() -> httpx.AsyncClient:
    settings = get_settings()
    return httpx.AsyncClient(
        base_url=settings.github_api_base_url, timeout=settings.github_request_timeout
    )


# Fixed, closed vocabulary -- never str(exc) or a traceback. Order matters:
# checked most-specific-first via isinstance, falling through to
# _UNEXPECTED_ERROR for anything not explicitly listed.
_SAFE_ERROR_MAPPING: dict[type[Exception], tuple[str, str]] = {
    GitHubRateLimitError: ("github_rate_limited", "GitHub API rate limit exceeded."),
    GitHubTimeoutError: ("github_timeout", "Timed out waiting for GitHub."),
    GitHubConnectionError: ("github_unavailable", "Could not reach GitHub."),
    GitHubServerError: ("github_server_error", "GitHub is currently unavailable."),
    GitHubResponseError: ("github_malformed_response", "GitHub returned an unexpected response."),
    GitHubAuthenticationError: ("github_auth_failed", "GitHub rejected our credentials."),
    GitHubRepositoryNotFoundError: ("github_repository_not_found", "GitHub repository not found."),
    SQLAlchemyError: ("database_error", "A database error occurred."),
}
_UNEXPECTED_ERROR = ("unexpected_error", "An unexpected error occurred.")


class _SyncTarget:
    """Immutable repository identity handed from Phase A to Phase B.

    Deliberately not the Repository ORM row itself -- that row belongs to
    the short-lived session Phase A already closed by the time Phase B's
    (potentially many, slow) GitHub HTTP calls run.
    """

    __slots__ = ("repository_id", "full_name")

    def __init__(self, repository_id: int, full_name: str) -> None:
        self.repository_id = repository_id
        self.full_name = full_name


async def run_sync_job(
    job_id_str: str,
    *,
    session_factory: SessionFactory = async_session_factory,
    http_client_factory: HttpClientFactory = _default_http_client_factory,
) -> None:
    """Execute one repository sync job, given only its id.

    Reconstructs everything else (which repository, GitHub client, DB
    session) from persisted state -- never touches a browser session, an
    OAuth token, or any object that crossed the queue boundary other than
    this UUID string. See app/services/sync_jobs.py for the enqueue side.
    """
    try:
        job_id = UUID(job_id_str)
    except ValueError:
        logger.error("Received malformed sync job id: %r", job_id_str)
        return

    settings = get_settings()
    try:
        # _mark_running is inside this try too: a database failure there is
        # exactly the "database failure -> job becomes failed" case, not
        # just failures during the GitHub-fetch/persist phase below it.
        target = await _mark_running(job_id, session_factory)
        if target is None:
            return

        # One httpx.AsyncClient per job -- explicit, self-cleaning, and
        # simple. Worker-wide client pooling can be revisited later if
        # throughput ever demands it (see README known limitations).
        async with http_client_factory() as http_client:
            github_client = GitHubClient(http_client, token=settings.github_token)
            # A fresh session opened right before the only call that needs
            # one; SQLAlchemy checks out its pooled connection lazily on
            # first use, so no database connection is held during the
            # GitHub HTTP calls inside sync_repository -- only during its
            # final persistence commit.
            async with session_factory() as db:
                pr_count, review_count = await sync_repository(
                    target.repository_id, target.full_name, db, github_client
                )
    except Exception as exc:
        logger.exception("Sync job %s failed.", job_id)
        # If the database itself is unreachable, this call will raise too --
        # an honest, accepted limitation (see README known limitations)
        # rather than something a second layer of retries can paper over.
        await _mark_failed(job_id, exc, session_factory)
        return

    await _mark_succeeded(job_id, pr_count, review_count, session_factory)


async def _mark_running(job_id: UUID, session_factory: SessionFactory) -> _SyncTarget | None:
    async with session_factory() as session:
        job = await session.get(SyncJob, job_id)
        if job is None:
            # The job's User or Repository was deleted (ON DELETE CASCADE
            # removes the job row itself) between enqueue and pickup.
            logger.warning("Sync job %s no longer exists; skipping.", job_id)
            return None
        if job.status != "queued":
            # Defensive: an at-least-once delivery redelivered a message
            # for a job already picked up (or finished) by a prior attempt.
            logger.warning("Sync job %s is not queued (status=%s); skipping.", job_id, job.status)
            return None

        # Guaranteed to exist: sync_jobs.repository_id is
        # ON DELETE CASCADE, so the job row cannot outlive its repository.
        repository = await session.get(Repository, job.repository_id)
        if repository is None:
            logger.warning("Sync job %s references a missing repository; skipping.", job_id)
            return None

        job.status = "running"
        job.started_at = datetime.now(UTC)
        await session.commit()
        return _SyncTarget(repository_id=repository.id, full_name=repository.full_name)


async def _mark_succeeded(
    job_id: UUID, pr_count: int, review_count: int, session_factory: SessionFactory
) -> None:
    async with session_factory() as session:
        job = await session.get(SyncJob, job_id)
        if job is None:
            logger.warning("Sync job %s no longer exists; cannot record success.", job_id)
            return
        job.status = "succeeded"
        job.pull_requests_processed = pr_count
        job.reviews_processed = review_count
        job.finished_at = datetime.now(UTC)
        await session.commit()


async def _mark_failed(job_id: UUID, exc: Exception, session_factory: SessionFactory) -> None:
    code, message = _safe_error_for(exc)
    async with session_factory() as session:
        job = await session.get(SyncJob, job_id)
        if job is None:
            logger.warning("Sync job %s no longer exists; cannot record failure.", job_id)
            return
        job.status = "failed"
        job.safe_error_code = code
        job.safe_error_message = message
        job.finished_at = datetime.now(UTC)
        await session.commit()


def _safe_error_for(exc: Exception) -> tuple[str, str]:
    for exc_type, mapping in _SAFE_ERROR_MAPPING.items():
        if isinstance(exc, exc_type):
            return mapping
    return _UNEXPECTED_ERROR


@dramatiq.actor(broker=broker, max_retries=0)
async def sync_repository_actor(job_id: str) -> None:
    """Thin Dramatiq wrapper around run_sync_job.

    max_retries=0 is explicit and deliberate: one queue delivery is one
    sync attempt. run_sync_job already catches every application failure
    and terminates the job as `failed`, so there is nothing left for
    Dramatiq's own retry middleware to usefully retry.
    """
    await run_sync_job(job_id)
