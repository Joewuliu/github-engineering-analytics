# GitHub Engineering Analytics Platform

## Project Goal

Build a production-quality backend application that allows users to connect
their GitHub account, ingest repository activity, and calculate engineering
analytics.

This project is primarily intended to demonstrate backend engineering skills.

## Planned Stack

- Python
- FastAPI
- PostgreSQL
- SQLAlchemy
- Alembic
- GitHub OAuth
- GitHub REST / GraphQL APIs
- GitHub Webhooks
- Redis
- Background jobs
- Docker
- pytest
- GitHub Actions
- Cloud deployment

## Development Philosophy

- Build incrementally.
- Do not introduce technology before there is a concrete reason for it.
- Prefer simple, production-quality architecture over unnecessary complexity.
- Do not use microservices unless the project eventually has a legitimate need.
- Keep business logic separate from API route handlers.
- Write tests for important behavior.
- Use type hints throughout the Python codebase.
- Explain significant architectural decisions before implementing them.

## Working With Claude

Before making a significant architectural change:

1. Explain the proposed change.
2. Explain why it is needed.
3. Identify the files that will change.
4. Then implement it.

Do not generate large portions of the application that have not been requested.

When completing a milestone:
- Run tests.
- Run linting/formatting checks.
- Report what changed.
- Mention any remaining issues.

## Current Milestone

Milestones 1–8 are complete:

- Milestone 1: FastAPI backend foundation.
- Milestone 2: PostgreSQL via Docker Compose, async SQLAlchemy 2.x, Alembic
  migrations, a `Repository` model, `GET /repositories`.
- Milestone 3: GitHub REST API integration. GitHub's response is treated as
  authoritative for `github_id`/canonical `full_name`. GitHub failures are
  mapped to safe, explicit HTTP statuses (404/502/503/504) with no tokens,
  stack traces, or raw upstream bodies exposed. `httpx` is a runtime
  dependency.
- Milestone 4: GitHub OAuth authentication ("Sign in with GitHub") with PKCE
  (RFC 7636, S256). Sessions are server-side, opaque, random tokens; only
  their SHA-256 hash is ever persisted (`app/core/security.py`) — the raw
  token lives only in an `HttpOnly` cookie and is never stored, logged, or
  returned. No `SESSION_SECRET` — nothing is signed. OAuth `state` and the
  PKCE `code_verifier` are both mandatory, cookie-carried, and validated
  before any token exchange. `User` is minimal (`github_id` authoritative,
  `github_login`) with no profile fields. The GitHub OAuth access token is
  used once (to identify the user) and discarded — never persisted.
- Milestone 5: per-user repository tracking. `Repository` remains a single,
  globally-deduplicated catalog (by `github_id`); a `user_repositories`
  table (composite PK `(user_id, repository_id)`, both FKs `ON DELETE
  CASCADE`) records which users track which repositories, with `tracked_at`
  as the only relationship metadata. `POST /me/repositories` replaced
  `POST /repositories` (no alias kept — no external clients yet); it returns
  201 whenever a new tracking relationship is created, whether the canonical
  `Repository` was new or already existed globally, and 409 only when the
  current user specifically already tracks it. `GET /me/repositories` and
  `DELETE /me/repositories/{repository_id}` are new, both authenticated;
  `GET /repositories` and `GET /health` remain public and unchanged.
  `app/services/repository_import.py` was renamed to
  `app/services/repositories.py`, split into `resolve_repository()`
  (canonical repository find-or-create, including race recovery) and
  `track_repository_for_user()`/`untrack_repository_for_user()`.
- Milestone 6: pull request and review ingestion. `PullRequest`
  (`UNIQUE(github_id)`, `UNIQUE(repository_id, number)`) and
  `PullRequestReview` (`UNIQUE(github_id)`, FK `ON DELETE CASCADE` to its PR)
  store GitHub event timestamps distinct from our own row timestamps;
  `author_login`/`reviewer_login` are nullable, mutable strings, never
  identity. `POST /me/repositories/{repository_id}/sync` (authenticated,
  same 404 for nonexistent-or-untracked as elsewhere) fetches the most
  recent `MAX_PULL_REQUESTS_PER_SYNC` = 25 pull requests (`state=all`,
  newest first — a deliberately small, named, easy-to-replace bound; see
  `app/services/repository_sync.py`) and every fetched PR's reviews via a
  small GitHub-specific `Link`-header pagination helper on `GitHubClient`,
  holds everything in memory, then persists all-or-nothing using
  PostgreSQL's native `INSERT ... ON CONFLICT DO UPDATE`
  (`sqlalchemy.dialects.postgresql.insert`) — a deliberate, local exception
  to the select-then-branch pattern used elsewhere, chosen for this
  ingestion workload's idempotency/round-trip needs. No analytics yet —
  schema only preserves what cycle-time and time-to-first-review will need.
- Milestone 7: repository engineering analytics. `GET
  /me/repositories/{repository_id}/metrics` (authenticated, same tracked/404
  semantics as sync) computes `total_pull_requests`, `merged_pull_requests`,
  `merge_rate`, `median_pr_cycle_time_hours`, and
  `median_time_to_first_review_hours` entirely from already-stored
  `PullRequest`/`PullRequestReview` rows — it has no `GitHubClient`
  dependency and makes zero GitHub calls, so an untouched-since-tracking
  repository still returns `200` with empty/null metrics rather than an
  error. The tracked-repository authorization check used by both `/sync` and
  `/metrics` was extracted from `repository_sync.py` into
  `get_tracked_repository()` in `app/services/repositories.py` (alongside
  `RepositoryNotTrackedError`, moved there too), so both endpoints share
  identical 401/404 behavior by construction. `app/services/
  repository_metrics.py` uses a hybrid SQL/Python split: SQL `COUNT` for
  totals, a targeted two-column `SELECT` for cycle-time, and one joined
  `SELECT` (no N+1) for first-review timestamps; Python does the datetime
  subtraction and `statistics.median` (never mean, never PostgreSQL
  `percentile_cont`). Medians are computed per-PR, not per-review — a PR
  with many reviews contributes exactly one first-review duration. Rows with
  a temporally impossible timestamp (`merged_at`/review `submitted_at`
  earlier than the PR's `github_created_at`) are excluded from their
  respective median rather than producing a negative duration. Nothing is
  rounded, cached, or persisted inside the service — rounding to 2 decimal
  places happens only at the API response boundary in
  `app/api/routes/repositories.py`. No schema change was required.
- Milestone 8: durable background repository sync jobs. `POST
  /me/repositories/{repository_id}/sync` no longer performs GitHub
  ingestion itself — it authorizes via `get_tracked_repository()`, creates a
  `SyncJob(status="queued")` row, and enqueues the job id (as a plain
  string, never an ORM object) to a Dramatiq actor, returning `202` with
  `{job_id, repository_id, status}`. `GET /me/sync-jobs/{job_id}`
  (authenticated, owner-only — same never-reveal-existence 404 for a
  nonexistent job and one owned by another user) polls status. Queue:
  Dramatiq + Redis (`app/worker/broker.py`, `RedisBroker` +
  the `AsyncIO` middleware so actors run as native `async def`), chosen
  over ARQ (maintenance-only) and Celery/RQ (fork-based worker models that
  fight Windows); Redis is transport only, no results backend — job status
  lives entirely in Postgres. The actor (`app/worker/tasks.py`) is
  registered with `max_retries=0`: one queue delivery is one sync attempt,
  deliberately no automatic retry this milestone. `sync_repository()`
  (`app/services/repository_sync.py`) was refactored to accept
  `repository_id`/`full_name` instead of a loaded `Repository` and a `user`
  — ingestion is now worker-only and never performs authorization itself.
  The worker never holds one long-lived database session: it opens and
  closes a separate short-lived `AsyncSession` (from the same
  `async_session_factory` the rest of the app uses) for each of marking the
  job `running`, performing ingestion, and marking it
  `succeeded`/`failed`, so nothing is held open across the many GitHub HTTP
  calls in between; similarly, one `httpx.AsyncClient` is opened per job via
  `async with`, not pooled worker-wide. `SyncJob`
  (`app/models/sync_job.py`) has a UUID primary key (client-facing job ids,
  to deter enumeration — the actual authorization boundary is the
  owner-only check), a `status` `CHECK` constraint restricting to
  `queued`/`running`/`succeeded`/`failed`, `ON DELETE CASCADE` FKs to both
  `users` and `repositories`, and a partial unique index
  (`repository_id` unique `WHERE status IN ('queued', 'running')`) that is
  the actual correctness boundary for "at most one active sync per
  canonical repository" — enforced at the database level even under
  concurrent requests, with an application-level pre-check in
  `app/services/sync_jobs.py` only for a cleaner error path and an
  `IntegrityError` catch as the race fallback. A duplicate active sync
  (regardless of which user owns the existing job — `Repository` is a
  single global row) returns `409` without revealing the existing job's id,
  since the caller might not own it and couldn't `GET` it anyway. An
  enqueue failure (Redis/Dramatiq unreachable) marks the just-created job
  `failed` (`safe_error_code="enqueue_failed"`) rather than leaving a
  phantom `queued` row, and returns `503`. Worker failures map to a small
  fixed `safe_error_code`/`safe_error_message` vocabulary (never
  `str(exception)` or a traceback) via an `isinstance`-ordered lookup in
  `app/worker/tasks.py`. A worker crash between `running` and a terminal
  state leaves that job stuck `running` forever (and, via the partial
  unique index, blocks future syncs for that repository) — a documented,
  accepted limitation; no heartbeat/stale-job sweep exists. `GET
  /me/repositories/{repository_id}/metrics` is unchanged and still makes
  zero GitHub calls. One additive migration (`sync_jobs` table only).
  `compose.yaml` gained a `redis:7-alpine` service (queue transport only,
  no persistence configured, no results backend). Local dev is three
  processes: `docker compose up -d` (Postgres + Redis), `uvicorn
  app.main:app --reload`, and `dramatiq app.worker.tasks --processes 1
  --threads 1` (one process/thread is a Milestone 8 choice for predictable
  GitHub rate-limit usage and easy manual observation, not an
  architectural limit — the partial unique index, not worker concurrency,
  is what enforces correctness). On Windows, `app/worker/broker.py`
  explicitly sets `WindowsSelectorEventLoopPolicy` before Dramatiq's
  `AsyncIO` middleware starts its event-loop thread, and
  `app/worker/tasks.py` explicitly imports every model module (mirroring
  `tests/conftest.py`/`alembic/env.py`) — both were real bugs caught only
  by running the actual worker process end-to-end against live
  Postgres/Redis, not by the (fully offline, Redis-free) automated test
  suite.

Do not implement ARQ, Celery, or RQ; background retries; scheduling/cron;
GitHub webhooks; job cancellation; job progress percentages; a job-listing
endpoint; stale-job recovery/heartbeats; Redis persistence tuning; additional
OAuth providers; password authentication; GitHub GraphQL; commit ingestion;
issue ingestion; contributors; organizations; team analytics; time-series/
trend metrics; percentile-based metrics (p90/p95/etc.); frontend/dashboard;
CI/CD; deployment infrastructure; app containerization; role-based
authorization; or an admin system until their respective milestones.