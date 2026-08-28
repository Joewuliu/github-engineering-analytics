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

Milestones 1–9 are complete:

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
- Milestone 9: reproducible Dockerized application + CI. No new product
  features — the goal was making the existing backend reproducible,
  containerized, and automatically tested. One application image (built
  from a single-stage `Dockerfile`, `python:3.14.7-slim-trixie`, non-root
  `appuser`, `pip install .` — never `.[dev]`, so pytest/ruff/mypy never
  enter the image) is used, with different `command:`s, by three new
  Compose services: `migrate` (`alembic upgrade head`, runs once and
  exits), `api` (`uvicorn app.main:app --host 0.0.0.0 --port 8000`), and
  `worker` (unchanged from Milestone 8: `dramatiq app.worker.tasks
  --processes 1 --threads 1`). `api`/`worker` depend on `migrate` via
  Compose's `service_completed_successfully` condition and on `redis`
  being healthy — migrations are never run inside either app process's own
  startup, avoiding both a startup race and mixing schema-management
  concerns into normal process startup. Inside Docker, `DATABASE_URL`/
  `REDIS_URL` are overridden to `db:5432`/`redis:6379` purely via
  `environment:` in `compose.yaml` — `app/config.py` still defaults to
  `localhost` (correct for local, non-Docker use), so no Docker hostname
  is ever hard-coded into application source. Added `GET /ready`
  (`app/services/readiness.py`), checking PostgreSQL only via `SELECT 1` —
  deliberately not GitHub (external, must never gate readiness) and not
  Redis (only needed to create a sync job, not to read repositories/
  metrics). The `api` container's Docker healthcheck stays pointed at the
  existing dependency-free `GET /health`, not `/ready` — a transient
  Postgres blip shouldn't flap the API container's own liveness status;
  that's what gates *startup order* via `migrate`, not ongoing liveness.
  Both the existing local workflow (`docker compose up -d db redis` +
  local `uvicorn`/`dramatiq`/`alembic`) and the new full-Docker workflow
  (`docker compose up --build`) are preserved side by side, sharing one
  `compose.yaml`/`.env` — nothing is duplicated between them.
  `requires-python`, `[tool.mypy] python_version`, and `[tool.ruff]
  target-version` were bumped from 3.12 to 3.14 to match the interpreter
  actually used for development, Docker, and CI (bumping
  `requires-python` narrows the package's declared compatible-version
  range, not merely a lint-config change); this incidentally made
  ruff's `UP037` flag five `Mapped["ClassName"]` quoted forward
  references in `app/models/` as removable, since Python 3.14 defers
  annotation evaluation by default (PEP 649) — the unquoted form was
  verified safe (full test suite + mypy strict, both green) before being
  applied, given quoted forward references to `TYPE_CHECKING`-only
  imports are exactly the kind of thing that broke a real, different
  SQLAlchemy name-resolution case during Milestone 8. CI
  (`.github/workflows/ci.yml`) is one workflow, two jobs: `quality` (a
  real `postgres:16-alpine` GitHub Actions service container seeded with
  only `github_analytics_test`, `DATABASE_URL` scoped to it, the existing
  `_test`-suffix pytest safety guard untouched and active, then `alembic
  upgrade head` / `alembic check` / a light `downgrade -1` + `upgrade
  head` reversibility smoke test / `pytest` / `ruff check .` / `ruff
  format --check .` / `mypy app` — no Redis service, matching the test
  suite's deliberate Redis-free design) and `docker-smoke` (`needs:
  quality`; builds the image, brings up the full Compose stack against a
  CI-only `.env` generated from `.env.example` with placeholder OAuth
  values — no real secrets, no GitHub calls, no OAuth login — polls
  `/health` with a bounded retry loop rather than a fixed sleep, then
  verifies `migrate` exited `0`, `worker` is still running, and the
  Dramatiq actor imports/registers via a standalone `docker run --rm
  <image> python -c "from app.worker.tasks import sync_repository_actor"`
  that needs no live Redis; always tears down with `docker compose down
  -v`). `quality` and `docker-smoke` are deliberately separate jobs so
  `docker-smoke`'s own Compose-managed `db` (host port 5432) never runs
  alongside `quality`'s GitHub Actions Postgres service (also port 5432)
  in the same job. `.dockerignore` excludes `.env`, `.git`, `tests/`, and
  all caches; verified directly (not just asserted) that the built image
  runs as a non-root user, contains no `.env`, and has no dev tooling
  (pytest/ruff/mypy) installed. No dependency lock file was introduced —
  the Python/OS layer is now pinned and reproducible
  (`python:3.14.7-slim-trixie`), but `pip install .` still resolves
  dependency versions against PyPI at build time, so builds are not
  byte-for-byte deterministic; documented as a known limitation rather
  than solved. No lock-file/dependency-manager migration, no reverse
  proxy/HTTPS, no cloud deployment, and no worker HTTP health check
  (deliberately) were introduced.

**Milestone 10 (public cloud deployment): deployment preparation
implemented; live Render deployment pending manual provisioning/
verification.** Milestone 10 is not complete — no Render resources have
been created, and there is no live URL. What's implemented so far:
`app/db/session.py`'s engine now sets `pool_pre_ping=True` (a cheap
liveness check before handing out a pooled connection, so a managed
Postgres instance silently dropping an idle connection surfaces as a
transparent reconnect rather than a confusing mid-request failure) —
`pool_size`/`max_overflow`/session architecture are unchanged, since
nothing at this project's scale (one API instance + one worker instance)
demonstrates a need to tune them. `tests/test_auth_routes.py` gained tests
proving `APP_ENV=production` actually produces `Secure` cookies (via
monkeypatching `app.api.routes.auth.get_settings`, since this behavior —
`_cookie_secure()` — already existed since Milestone 4 but had no direct
test coverage) alongside a paired test proving `Secure` is *absent* by
default; `tests/test_db.py` gained a small configuration-guard test
(`engine.pool._pre_ping is True`) rather than attempting to simulate actual
stale-connection recovery. Render was confirmed (not merely assumed) as the
target platform, with corrected terminology and cost assumptions from the
original plan: the product is **Render Key Value** (Valkey-backed, Redis-
client-compatible), not "managed Redis"; Background Workers have no free
instance type, Pre-Deploy Commands require a paid tier, and free Postgres
is unsuitable for durable data — the README states this as "verify current
Render pricing before provisioning" rather than hard-coding dollar figures
that will drift. `README.md`'s new "Production deployment" section
documents the full manual setup sequence (provision Postgres/Key Value →
deploy API with `alembic upgrade head` as its Pre-Deploy Command → verify
`/health`/`/ready` → only then deploy the worker), why the API exclusively
owns migrations (never the worker, never inside any process's own start
command), the `DATABASE_URL` scheme caveat (Render may display
`postgresql://`; this app's async SQLAlchemy + psycopg3 stack needs
`postgresql+psycopg://`), the `PORT`-via-`${PORT:-10000}` Render Start
Command (the Dockerfile's own `CMD`/local Compose's port 8000 are
unchanged), the separate production-only GitHub OAuth App, and exact
(non-interactive, no-token-paste) instructions for creating a fine-grained,
public-repositories-read-only, expiring `GITHUB_TOKEN`. Deliberately not
done in this step: no `render.yaml` (this first deployment is being proven
manually before being codified — see README), no GitHub Actions deploy
step or Render credentials in CI, auto-deploy left OFF on both services.
The CI workflow itself is unchanged from Milestone 9.

**Deployment decision, still within Milestone 10 preparation (live
deployment still in progress, not complete)**: the free public Render demo
will run on free resources and will have **no Background Worker at all** —
Render's free tier has no Background Worker instance type, so provisioning
one there would require a paid tier. To stop the free demo from accepting
sync requests it could never process, a new deployment-capability setting,
`background_sync_enabled: bool = True` (env var `BACKGROUND_SYNC_ENABLED`,
default `true`), was added to `app/config.py` — deliberately not inferred
from `app_env`, since `APP_ENV=production` does not itself mean sync is
unavailable; only a deployment with no worker does. The check lives in
`create_sync_job()` (`app/services/sync_jobs.py`), running immediately
after `get_tracked_repository()` (so nonexistent/untracked repositories
still return the identical 404 either way — the flag is never a way to
probe repository existence) but before the active-job check, before any
`SyncJob` row is created, and before `enqueue()` is ever called. A new
`BackgroundSyncDisabledError`, mapped centrally in `app/api/errors.py` like
every other domain exception, produces `503
{"detail": "Background synchronization is unavailable in this
deployment."}` — deliberately deployment-neutral wording, no provider name.
Local development, Docker Compose (both Option A and Option B), and the
full test suite are all unaffected — the flag's default is `true`, so the
complete API → Redis → Dramatiq worker → GitHub → PostgreSQL architecture
remains exactly as Milestone 8 designed and continues to be exercised by
the existing worker/job tests. `GET /me/sync-jobs/{job_id}` and
`GET /me/repositories/{id}/metrics` are both completely untouched by this
flag. No database migration was needed (a plain `Settings` field, no schema
involved). No render.yaml, deploy, or commit was made for this change
either.

Do not implement ARQ, Celery, or RQ; background retries; scheduling/cron;
GitHub webhooks; job cancellation; job progress percentages; a job-listing
endpoint; stale-job recovery/heartbeats; Redis persistence tuning; additional
OAuth providers; password authentication; GitHub GraphQL; commit ingestion;
issue ingestion; contributors; organizations; team analytics; time-series/
trend metrics; percentile-based metrics (p90/p95/etc.); frontend/dashboard;
cloud deployment on AWS/GCP/Azure, Kubernetes, Terraform, Helm, a reverse
proxy/nginx, a custom domain, a dependency-manager migration, an
observability platform, role-based authorization, an admin system,
`render.yaml`/infrastructure-as-code, automatic/CI-triggered deployment, or
actually creating any Render resource, until their respective milestones or
until Milestone 10's manual deployment is explicitly performed.