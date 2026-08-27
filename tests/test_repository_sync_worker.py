import inspect
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.worker.tasks as tasks_module
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.sync_job import SyncJob
from app.models.user import User
from app.worker.tasks import HttpClientFactory, SessionFactory, run_sync_job, sync_repository_actor


def _pr(id_: int, number: int, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": id_,
        "number": number,
        "title": f"PR {number}",
        "state": "open",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-02T00:00:00Z",
        "closed_at": None,
        "merged_at": None,
        "user": {"login": "octocat"},
    }
    payload.update(overrides)
    return payload


def _review(id_: int, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": id_,
        "state": "APPROVED",
        "submitted_at": "2024-01-03T00:00:00Z",
        "user": {"login": "reviewer"},
    }
    payload.update(overrides)
    return payload


def _sync_handler(
    prs: list[dict[str, object]],
    reviews_by_number: dict[int, list[dict[str, object]]] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    reviews_by_number = reviews_by_number or {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/reviews"):
            number = int(path.split("/")[-2])
            return httpx.Response(200, json=reviews_by_number.get(number, []))
        if path.endswith("/pulls"):
            return httpx.Response(200, json=prs)
        return httpx.Response(404, json={"message": "not found"})

    return handler


def _http_client_factory(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[HttpClientFactory, list[httpx.AsyncClient]]:
    created: list[httpx.AsyncClient] = []

    def factory() -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://api.github.com"
        )
        created.append(client)
        return client

    return factory, created


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


async def _make_queued_job(db_session: AsyncSession, user: User, repository: Repository) -> SyncJob:
    job = SyncJob(user_id=user.id, repository_id=repository.id, status="queued")
    db_session.add(job)
    await db_session.commit()
    return job


async def test_worker_happy_path_transitions_queued_to_succeeded(
    db_session: AsyncSession, db_session_factory: SessionFactory
) -> None:
    user = await _make_user(db_session, 1, "octocat")
    repository = await _make_repository(db_session, 1, "octocat/repo-a")
    job = await _make_queued_job(db_session, user, repository)

    handler = _sync_handler([_pr(1001, 1), _pr(1002, 2)], {1: [_review(2001)], 2: []})
    http_client_factory, _clients = _http_client_factory(handler)

    await run_sync_job(
        str(job.id), session_factory=db_session_factory, http_client_factory=http_client_factory
    )

    # populate_existing=True forces a fresh read past db_session's identity
    # map -- the worker committed via an entirely separate session (bound to
    # the same underlying test connection), and expire_on_commit=False means
    # a plain .get() here would otherwise silently return the stale,
    # already-loaded `job` object instead of its current row.
    refreshed = await db_session.get(SyncJob, job.id, populate_existing=True)
    assert refreshed is not None
    assert refreshed.status == "succeeded"
    assert refreshed.pull_requests_processed == 2
    assert refreshed.reviews_processed == 1
    assert refreshed.started_at is not None
    assert refreshed.finished_at is not None
    assert refreshed.safe_error_code is None

    prs = await db_session.execute(
        select(PullRequest).where(PullRequest.repository_id == repository.id)
    )
    assert len(prs.scalars().all()) == 2


async def test_worker_calls_ingestion_exactly_once(
    db_session: AsyncSession,
    db_session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await _make_user(db_session, 2, "octocat")
    repository = await _make_repository(db_session, 2, "octocat/repo-b")
    job = await _make_queued_job(db_session, user, repository)

    real_sync_repository = tasks_module.sync_repository
    calls: list[int] = []

    async def spy(*args: object, **kwargs: object) -> tuple[int, int]:
        calls.append(1)
        return await real_sync_repository(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(tasks_module, "sync_repository", spy)

    http_client_factory, _clients = _http_client_factory(_sync_handler([]))
    await run_sync_job(
        str(job.id), session_factory=db_session_factory, http_client_factory=http_client_factory
    )

    assert len(calls) == 1


async def test_worker_closes_database_sessions(
    db_session: AsyncSession,
    db_session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await _make_user(db_session, 3, "octocat")
    repository = await _make_repository(db_session, 3, "octocat/repo-c")
    job = await _make_queued_job(db_session, user, repository)

    close_calls = 0
    original_close = AsyncSession.close

    async def spy_close(self: AsyncSession) -> None:
        nonlocal close_calls
        close_calls += 1
        await original_close(self)

    http_client_factory, _clients = _http_client_factory(_sync_handler([_pr(1003, 1)], {1: []}))

    # Patched only around the call under test -- db_session's own fixture
    # teardown (its close()) happens after the test body returns, so it
    # never contaminates this count.
    monkeypatch.setattr(AsyncSession, "close", spy_close)
    await run_sync_job(
        str(job.id), session_factory=db_session_factory, http_client_factory=http_client_factory
    )

    # Phase A (mark running), Phase B (ingestion), Phase C (mark succeeded):
    # three independent short-lived sessions, none held across the GitHub calls.
    assert close_calls == 3


async def test_worker_closes_http_client(
    db_session: AsyncSession, db_session_factory: SessionFactory
) -> None:
    user = await _make_user(db_session, 4, "octocat")
    repository = await _make_repository(db_session, 4, "octocat/repo-d")
    job = await _make_queued_job(db_session, user, repository)

    http_client_factory, clients = _http_client_factory(_sync_handler([]))

    await run_sync_job(
        str(job.id), session_factory=db_session_factory, http_client_factory=http_client_factory
    )

    assert len(clients) == 1
    assert clients[0].is_closed


async def test_worker_github_rate_limit_marks_job_failed_with_safe_code(
    db_session: AsyncSession, db_session_factory: SessionFactory
) -> None:
    user = await _make_user(db_session, 5, "octocat")
    repository = await _make_repository(db_session, 5, "octocat/repo-e")
    job = await _make_queued_job(db_session, user, repository)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers={"X-RateLimit-Remaining": "0"}, json={})

    http_client_factory, _clients = _http_client_factory(handler)
    await run_sync_job(
        str(job.id), session_factory=db_session_factory, http_client_factory=http_client_factory
    )

    # populate_existing=True forces a fresh read past db_session's identity
    # map -- the worker committed via an entirely separate session (bound to
    # the same underlying test connection), and expire_on_commit=False means
    # a plain .get() here would otherwise silently return the stale,
    # already-loaded `job` object instead of its current row.
    refreshed = await db_session.get(SyncJob, job.id, populate_existing=True)
    assert refreshed is not None
    assert refreshed.status == "failed"
    assert refreshed.safe_error_code == "github_rate_limited"
    assert refreshed.safe_error_message is not None
    assert refreshed.finished_at is not None


def _not_found_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(404, json={"message": "Not Found"})


def _unauthorized_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(401, json={"message": "Bad credentials"})


def _server_error_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, json={"message": "boom"})


@pytest.mark.parametrize(
    ("index", "handler", "expected_code"),
    [
        (6, _not_found_handler, "github_repository_not_found"),
        (7, _unauthorized_handler, "github_auth_failed"),
        (8, _server_error_handler, "github_server_error"),
    ],
)
async def test_worker_github_errors_map_to_safe_codes(
    db_session: AsyncSession,
    db_session_factory: SessionFactory,
    index: int,
    handler: Callable[[httpx.Request], httpx.Response],
    expected_code: str,
) -> None:
    user = await _make_user(db_session, index, "octocat")
    repository = await _make_repository(db_session, index, f"octocat/repo-{index}")
    job = await _make_queued_job(db_session, user, repository)

    http_client_factory, _clients = _http_client_factory(handler)
    await run_sync_job(
        str(job.id), session_factory=db_session_factory, http_client_factory=http_client_factory
    )

    # populate_existing=True forces a fresh read past db_session's identity
    # map -- the worker committed via an entirely separate session (bound to
    # the same underlying test connection), and expire_on_commit=False means
    # a plain .get() here would otherwise silently return the stale,
    # already-loaded `job` object instead of its current row.
    refreshed = await db_session.get(SyncJob, job.id, populate_existing=True)
    assert refreshed is not None
    assert refreshed.status == "failed"
    assert refreshed.safe_error_code == expected_code


async def test_worker_timeout_marks_job_failed(
    db_session: AsyncSession, db_session_factory: SessionFactory
) -> None:
    user = await _make_user(db_session, 9, "octocat")
    repository = await _make_repository(db_session, 9, "octocat/repo-timeout")
    job = await _make_queued_job(db_session, user, repository)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    http_client_factory, _clients = _http_client_factory(handler)
    await run_sync_job(
        str(job.id), session_factory=db_session_factory, http_client_factory=http_client_factory
    )

    # populate_existing=True forces a fresh read past db_session's identity
    # map -- the worker committed via an entirely separate session (bound to
    # the same underlying test connection), and expire_on_commit=False means
    # a plain .get() here would otherwise silently return the stale,
    # already-loaded `job` object instead of its current row.
    refreshed = await db_session.get(SyncJob, job.id, populate_existing=True)
    assert refreshed is not None
    assert refreshed.status == "failed"
    assert refreshed.safe_error_code == "github_timeout"


async def test_worker_malformed_github_response_marks_job_failed(
    db_session: AsyncSession, db_session_factory: SessionFactory
) -> None:
    user = await _make_user(db_session, 10, "octocat")
    repository = await _make_repository(db_session, 10, "octocat/repo-malformed")
    job = await _make_queued_job(db_session, user, repository)

    http_client_factory, _clients = _http_client_factory(_sync_handler([{"id": 1}]))
    await run_sync_job(
        str(job.id), session_factory=db_session_factory, http_client_factory=http_client_factory
    )

    # populate_existing=True forces a fresh read past db_session's identity
    # map -- the worker committed via an entirely separate session (bound to
    # the same underlying test connection), and expire_on_commit=False means
    # a plain .get() here would otherwise silently return the stale,
    # already-loaded `job` object instead of its current row.
    refreshed = await db_session.get(SyncJob, job.id, populate_existing=True)
    assert refreshed is not None
    assert refreshed.status == "failed"
    assert refreshed.safe_error_code == "github_malformed_response"


async def test_worker_database_failure_marks_job_failed_with_database_error_code(
    db_session: AsyncSession, db_session_factory: SessionFactory
) -> None:
    user = await _make_user(db_session, 11, "octocat")
    repository = await _make_repository(db_session, 11, "octocat/repo-db-failure")
    job = await _make_queued_job(db_session, user, repository)

    # A PR already exists at (repository, number=1) under a different
    # github_id -- ingestion's upsert then raises a genuine IntegrityError,
    # a real database failure rather than a mocked one.
    db_session.add(
        PullRequest(
            github_id=9001,
            repository_id=repository.id,
            number=1,
            author_login="octocat",
            title="original",
            state="open",
            github_created_at=datetime(2024, 1, 1, tzinfo=UTC),
            github_updated_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
    )
    await db_session.commit()

    http_client_factory, _clients = _http_client_factory(_sync_handler([_pr(9002, 1)]))
    await run_sync_job(
        str(job.id), session_factory=db_session_factory, http_client_factory=http_client_factory
    )

    # populate_existing=True forces a fresh read past db_session's identity
    # map -- the worker committed via an entirely separate session (bound to
    # the same underlying test connection), and expire_on_commit=False means
    # a plain .get() here would otherwise silently return the stale,
    # already-loaded `job` object instead of its current row.
    refreshed = await db_session.get(SyncJob, job.id, populate_existing=True)
    assert refreshed is not None
    assert refreshed.status == "failed"
    assert refreshed.safe_error_code == "database_error"


async def test_worker_unexpected_exception_marks_job_failed_with_unexpected_error(
    db_session: AsyncSession,
    db_session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await _make_user(db_session, 12, "octocat")
    repository = await _make_repository(db_session, 12, "octocat/repo-unexpected")
    job = await _make_queued_job(db_session, user, repository)

    async def boom(*args: object, **kwargs: object) -> tuple[int, int]:
        raise RuntimeError("something truly unexpected")

    monkeypatch.setattr(tasks_module, "sync_repository", boom)

    http_client_factory, _clients = _http_client_factory(_sync_handler([]))
    await run_sync_job(
        str(job.id), session_factory=db_session_factory, http_client_factory=http_client_factory
    )

    # populate_existing=True forces a fresh read past db_session's identity
    # map -- the worker committed via an entirely separate session (bound to
    # the same underlying test connection), and expire_on_commit=False means
    # a plain .get() here would otherwise silently return the stale,
    # already-loaded `job` object instead of its current row.
    refreshed = await db_session.get(SyncJob, job.id, populate_existing=True)
    assert refreshed is not None
    assert refreshed.status == "failed"
    assert refreshed.safe_error_code == "unexpected_error"
    assert "something truly unexpected" not in (refreshed.safe_error_message or "")


async def test_worker_nonexistent_job_is_a_safe_no_op(db_session_factory: SessionFactory) -> None:
    http_client_factory, clients = _http_client_factory(_sync_handler([]))

    await run_sync_job(
        str(uuid4()), session_factory=db_session_factory, http_client_factory=http_client_factory
    )

    # Never even reached the GitHub-fetching phase.
    assert clients == []


async def test_worker_job_deleted_via_cascade_between_enqueue_and_pickup_is_a_safe_no_op(
    db_session: AsyncSession, db_session_factory: SessionFactory
) -> None:
    user = await _make_user(db_session, 13, "octocat")
    repository = await _make_repository(db_session, 13, "octocat/repo-deleted")
    job = await _make_queued_job(db_session, user, repository)
    job_id = job.id

    # Deleting the repository cascades to the job row itself.
    await db_session.delete(repository)
    await db_session.commit()
    assert await db_session.get(SyncJob, job_id, populate_existing=True) is None

    http_client_factory, clients = _http_client_factory(_sync_handler([]))
    await run_sync_job(
        str(job_id), session_factory=db_session_factory, http_client_factory=http_client_factory
    )

    # Did not crash, and never reached the GitHub-fetching phase.
    assert clients == []


async def test_worker_malformed_job_id_string_is_a_safe_no_op(
    db_session_factory: SessionFactory,
) -> None:
    http_client_factory, clients = _http_client_factory(_sync_handler([]))

    await run_sync_job(
        "not-a-uuid", session_factory=db_session_factory, http_client_factory=http_client_factory
    )

    assert clients == []


def test_run_sync_job_takes_no_browser_session_or_credentials() -> None:
    """Structural proof that the worker cannot depend on a browser session,
    User, or OAuth token -- its only input is a job id string."""
    parameters = set(inspect.signature(run_sync_job).parameters)
    assert parameters == {"job_id_str", "session_factory", "http_client_factory"}


def test_sync_repository_actor_has_no_automatic_retries() -> None:
    assert sync_repository_actor.options.get("max_retries") == 0
