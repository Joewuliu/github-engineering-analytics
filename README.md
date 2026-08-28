# GitHub Engineering Analytics Platform

A backend application that connects to GitHub, ingests repository activity,
and calculates engineering analytics. This project is intended to demonstrate
production-quality backend engineering practices.

## Current Status: Milestone 11 (implemented, not deployed) — Frontend MVP

Milestone 1 established the base FastAPI application. Milestone 2 added
PostgreSQL, async SQLAlchemy 2.x, and Alembic migrations. Milestone 3 added
GitHub REST API integration. Milestone 4 added "Sign in with GitHub" (OAuth
with PKCE, server-side sessions). Milestone 5 introduced per-user repository
tracking. Milestone 6 ingested pull requests and reviews. Milestone 7 turned
that stored data into engineering metrics via
`GET /me/repositories/{repository_id}/metrics` — computed entirely from
PostgreSQL, no GitHub calls. Milestone 8 moved repository sync off the
request/response cycle via a Dramatiq background worker and Redis queue.
Milestone 9 made the existing backend reproducible: the whole stack (API,
worker, Postgres, Redis) runs through one Docker Compose command, backed by a
single application image, a dedicated migration step, `/ready` for
orchestration-aware readiness, and a GitHub Actions CI workflow. Milestone
10's code/configuration preparation for a public Render deployment is
implemented — `pool_pre_ping` for managed-database reliability, cookie
`Secure` behavior under `APP_ENV=production`, and a
`BACKGROUND_SYNC_ENABLED` deployment-capability flag (default `true`) that
lets a Background-Worker-less deployment safely reject sync requests with
`503` — documented in [Production deployment](#production-deployment) below.
**Milestone 11 adds a React + TypeScript frontend, served by FastAPI itself
at `/` in production (same origin, no CORS) — implemented and passing its
own test suite, but not yet deployed.** See [Frontend](#frontend) below for
the local dev workflow, the same-origin serving strategy, and how it's built
into the Docker image. **The application is not yet reachable at a public
URL** — that requires manually provisioning Render resources, which has not
been done. GitHub GraphQL, webhooks, scheduling, and further analytics still
do not exist — those arrive in later milestones.

## Quick start

The fastest way to run the whole application, for someone evaluating this
repository who doesn't want to set up a Python environment:

```bash
git clone <this repo> && cd github-engineering-analytics
cp .env.example .env
docker compose up --build
```

Wait for `api` to report healthy (`docker compose ps`), then visit
http://127.0.0.1:8000/ for the frontend (built as part of the image, see
[Frontend](#frontend)) or http://127.0.0.1:8000/docs for the API directly.
See [Running the application](#running-the-application) for what actually
happens during that command, and [Option B](#option-b-local-development) for
the faster-iteration alternative used for active development.

## Requirements

- Docker and Docker Compose — the only requirement for the [Quick start](#quick-start) above.
- Python 3.14+ — only needed for [Option B](#option-b-local-development) (running `uvicorn`/`dramatiq`/`pytest` directly) or contributing.
- Node.js — only needed for [local frontend development](#local-frontend-development) (Docker builds the frontend for you otherwise).

## Setup

Create and activate a virtual environment, then install the project with dev
dependencies (skip this if you're only using the full-Docker workflow):

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # macOS / Linux

pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and adjust values as needed:

```bash
cp .env.example .env
```

## Running the application

Two supported workflows, sharing the same `compose.yaml` and the same `.env`
— nothing is duplicated between them, just a different subset of services.

### Option A: full Docker Compose

```bash
docker compose up --build
```

This builds one application image and starts, in dependency order:
`db` (Postgres) and `redis` become healthy → `migrate` runs `alembic upgrade
head` and exits → `api` and `worker` start, using that same image with
different commands. See [Service architecture](#service-architecture) and
[Migration service](#migration-service) below for exactly how that ordering
is enforced. The API is available at http://127.0.0.1:8000 (docs at `/docs`),
identical to Option B from the outside. Best for evaluating the repository or
proving "this is what would actually run" — the image is immutable, no bind
mounts, no `--reload`.

### Option B: local development

```bash
docker compose up -d db redis
```

then, in separate terminals:

```bash
uvicorn app.main:app --reload
dramatiq app.worker.tasks --processes 1 --threads 1
```

and, once (or after adding a migration):

```bash
alembic upgrade head
```

Best for active development — code changes take effect on save (`--reload`)
without rebuilding an image. This is unchanged from Milestone 8.

Note for Windows: `uvicorn --reload` works as-is because `--reload` runs the
server in a subprocess, and uvicorn selects `SelectorEventLoop` for that case
— which is what psycopg's async driver requires. Running uvicorn *without*
`--reload` on Windows would hit the default `ProactorEventLoop`, which
psycopg cannot use — irrelevant inside the Docker image (Option A), since
Linux's default loop is already compatible; this note only applies to running
uvicorn directly on Windows.

## Service architecture

```
db (Postgres) ──┐
                 ├─▶ migrate (alembic upgrade head, then exits)
redis ───────────┤        │
                 │        ▼
                 └─▶ api ─────▶ worker
```

`api` and `worker` are the **same application image**
(`github-engineering-analytics:local`), started with different `command:` —
`uvicorn app.main:app --host 0.0.0.0 --port 8000` vs.
`dramatiq app.worker.tasks --processes 1 --threads 1`. `migrate` is a
one-off use of that same image. Compose's default per-project network is
used — no custom network is needed since all five services already resolve
each other by service name. Only `api` publishes a port to the host
(`8000:8000`); `db` (`5432`) and `redis` (`6379`) also publish theirs, purely
for host-side convenience (`psql`, `redis-cli`, or Option B's local
processes) — `worker` publishes nothing.

## Migration service

`migrate` runs `alembic upgrade head` once and exits — it is not run inside
either `api`'s or `worker`'s own startup command. Both deliberately embedding
`alembic upgrade head` into two different processes' startup would risk two
containers racing to apply DDL concurrently and would mix schema-management
concerns into normal process startup; a dedicated one-off service keeps
migration ownership explicit and singular. `api`/`worker` declare
`depends_on: migrate: condition: service_completed_successfully` — Compose
will not start either until `migrate` has exited with status `0`. To run
migrations without starting the rest of the stack (e.g. after pulling a new
migration):

```bash
docker compose run --rm migrate
```

### Known limitations (Docker/CI)

- **No dependency lock file**: the image installs from `pyproject.toml`'s
  version *ranges* against PyPI at build time. The Python/OS layer is now
  pinned and reproducible (`python:3.14.7-slim-trixie`), but dependency
  resolution is not byte-for-byte deterministic build-to-build. Introducing
  a lock file (or switching dependency managers) is out of scope for this
  milestone.
- **`worker` has no health check**, deliberately — nothing else `depends_on`
  it being "healthy" in a deeper sense than "the container is running," and
  a fake HTTP server purely to give Compose something to poll would be
  needless complexity. `docker compose ps`/`logs worker` plus its `restart:
  unless-stopped` policy are what's actually available for observing it.
- **`docker-smoke`'s timing** depends on the GitHub Actions runner and image
  pull speed on a given run; its health-poll uses a bounded retry loop
  rather than a fixed sleep specifically to absorb that variance, but a
  genuinely overloaded runner could still make it the slowest, most
  failure-prone step in CI.

## Health and readiness

- `GET /health` — dependency-free liveness. Always fast, never touches
  Postgres/Redis/GitHub. This is what the `api` container's Docker
  `healthcheck:` polls — a transient Postgres blip shouldn't make Compose
  think the API *container itself* needs restarting; that's Postgres's own
  healthcheck's job to gate *startup order* (via `migrate`), not something
  that should flap the API's ongoing liveness status.
- `GET /ready` — checks PostgreSQL only, via a plain `SELECT 1`: `200` if
  reachable, `503` if not. Deliberately does **not** check GitHub (external,
  expected to be occasionally unavailable, must never gate our own
  readiness) or Redis (only needed to create a sync job — not for listing
  repositories or reading metrics, so it shouldn't make the whole API report
  unready over one write path).

## Database and queue (PostgreSQL + Redis)

`compose.yaml`'s `db` service creates two databases on first start:
`github_analytics` (development — the only one `api`/`worker`/`migrate` ever
use) and `github_analytics_test` (used only by the test suite, via
`docker/postgres/init-test-db.sql`), kept isolated exactly as before.
`redis` (`redis:7-alpine`) is used purely as the Dramatiq queue transport
(see [Syncing pull requests and reviews](#syncing-pull-requests-and-reviews-background))
— no persistence configured, holding nothing that needs to survive a restart
except in-flight queued jobs.

```bash
docker compose up -d db redis     # infrastructure only (Option B)
docker compose stop               # stop everything -- Postgres data persists in the pgdata volume
docker compose down                # stop and remove containers -- pgdata volume still persists
docker compose down -v             # stop and remove EVERYTHING, including the pgdata volume
```

**`docker compose down -v` permanently deletes your local Postgres data.**
Use plain `docker compose down` (or `docker compose stop`) to shut down
without losing tracked repositories, synced PRs/reviews, or sessions — reach
for `-v` only when you deliberately want a completely clean slate.

### Container-to-container URLs

Inside Docker, `localhost` does not mean "another service" — so `api`,
`worker`, and `migrate` are given `DATABASE_URL`/`REDIS_URL` pointing at
`db:5432`/`redis:6379` via `environment:` overrides in `compose.yaml`, which
take precedence over the same keys loaded from `.env`. No application code
hard-codes these hostnames — `Settings` (`app/config.py`) still defaults to
`localhost`, which is what's correct for Option B's local Python processes.
The only place `db`/`redis` hostnames ever appear is `compose.yaml`.

### Migrations

Alembic reads the database URL from `Settings` (`app/config.py`), not from
`alembic.ini`, so `.env`/`DATABASE_URL` is the single source of truth.

```bash
alembic upgrade head              # apply all migrations
alembic revision --autogenerate -m "description"   # generate a new migration
```

## GitHub API access

`POST /me/repositories` looks the repository up on the real GitHub REST API
(`https://api.github.com`) before persisting/tracking it. `GITHUB_TOKEN` is
optional — public repositories work without one. Set it in your local
(gitignored) `.env` only if you want GitHub's higher authenticated rate limit
(5000 requests/hour instead of 60); never commit a real token. A shared
`httpx.AsyncClient` is created once in the FastAPI lifespan and closed on
shutdown, so repeated requests reuse pooled connections.

### Repositories: global catalog vs. per-user tracking

`Repository` rows are a single, global, deduplicated catalog keyed by
GitHub's numeric id — every user who tracks `fastapi/fastapi` shares the
*same* row. A separate `user_repositories` table records which users track
which repositories; deleting a tracking relationship never deletes the
canonical `Repository`, and a repository stays in the global catalog even
after everyone stops tracking it (no garbage collection).

| Endpoint | Auth | Behavior |
|---|---|---|
| `GET /repositories` | No | Public, global catalog of every tracked-by-someone repository |
| `POST /me/repositories` | Yes | Track a repository for the current user (see below) |
| `GET /me/repositories` | Yes | List repositories the current user tracks, most recently tracked first |
| `DELETE /me/repositories/{repository_id}` | Yes | Stop tracking a repository (idempotent) |
| `POST /me/repositories/{repository_id}/sync` | Yes | Enqueue a background sync job for a tracked repository (see below) |
| `GET /me/sync-jobs/{job_id}` | Yes | Poll a sync job's status (see below) |
| `GET /me/repositories/{repository_id}/metrics` | Yes | Compute engineering metrics from stored data (see below) |

### Tracking a repository

```bash
curl -X POST http://127.0.0.1:8000/me/repositories \
  -H "Content-Type: application/json" \
  --cookie "session=<your session cookie value>" \
  -d '{"full_name": "fastapi/fastapi"}'
```

- **201 Created** — a *new tracking relationship* was created. This happens
  both when the canonical `Repository` didn't exist yet (it's created) and
  when it already existed globally because someone else tracks it (it's
  reused) — either way, from the caller's perspective they now track it,
  which is the resource being created. The response's `full_name` is
  GitHub's canonical casing, which may differ from what you submitted.
- **401 Unauthorized** — no valid session.
- **409 Conflict** — *you* already track this repository (checked by GitHub's
  numeric id, not the submitted string, so resubmitting with different
  casing still resolves to the same conflict). Someone else tracking it is
  never a conflict.
- **404** — GitHub has no such repository.
- **502/503/504** — GitHub (or the network) failed in some way; see
  `app/api/errors.py` for the full mapping. Responses never include GitHub
  tokens, raw upstream bodies, or stack traces.

### Listing your tracked repositories

```bash
curl http://127.0.0.1:8000/me/repositories --cookie "session=..."
```

Each entry is the repository plus `tracked_at` (when you started tracking
it) — the one piece of information the tracking relationship adds beyond the
plain `Repository` shape.

### Untracking a repository

```bash
curl -X DELETE http://127.0.0.1:8000/me/repositories/<repository_id> --cookie "session=..."
```

**204** whether or not you were tracking it — idempotent, same precedent as
`POST /auth/logout`. Only your tracking relationship is removed; the
canonical `Repository` is never touched.

### Listing the global catalog (public)

```bash
curl http://127.0.0.1:8000/repositories
```

## Syncing pull requests and reviews (background)

Sync fetches this repository's pull requests from GitHub (`state=all`,
newest first) and every fetched PR's reviews, then persists them. As of
Milestone 8 this happens in a background worker, not inside the HTTP
request — the endpoint only creates a durable job record and hands it to a
queue.

**Bounded to the most recent `MAX_PULL_REQUESTS_PER_SYNC` = 25 pull requests**
(`app/services/repository_sync.py`) — deliberately small and easy to find/
replace later. Each PR needs its own separate GitHub "list reviews" request,
so a larger limit multiplies outbound API calls quickly; 25 keeps a sync
usable even without `GITHUB_TOKEN` configured (GitHub's unauthenticated limit
is 60 requests/hour). Real full-history or incremental sync is deferred to a
later milestone.

All GitHub fetching (the PR list and every PR's reviews) completes and is
held in memory *before* any database write begins; persistence then happens
as a single all-or-nothing transaction — a failure at any point during
GitHub fetching leaves no partial writes, and a database failure during
persistence rolls back the entire sync. "Processed" means examined/upserted,
not newly inserted — running the same sync twice against unchanged GitHub
data produces the same counts but zero new rows. Re-syncing updates a PR's
mutable fields (`state`, `title`, `author_login`, `closed_at`, `merged_at`,
`github_updated_at`) and a review's (`state`, `reviewer_login`,
`submitted_at`) in place; identity fields (`github_id`, `repository_id`,
`number`, `pull_request_id`, `github_created_at`) never change. Upserts use
PostgreSQL's native `INSERT ... ON CONFLICT DO UPDATE` (via
`sqlalchemy.dialects.postgresql.insert`), not a select-then-branch pattern —
a deliberate, local exception to the pattern used elsewhere in the codebase,
chosen for this ingestion workload's idempotency and round-trip-count needs.

### Why background sync

A sync makes up to 26 outbound GitHub requests (1 PR list + up to 25 review
lists) fully serially. Running that inside the HTTP request/response cycle
ties the request's lifetime to GitHub's availability and latency, and blocks
the calling thread/connection for however long that takes. Milestone 8 moves
that work to a Dramatiq worker process: the HTTP request now only creates a
job row and enqueues it, returning in milliseconds regardless of how long the
actual sync takes.

### Starting a sync

```bash
curl -X POST http://127.0.0.1:8000/me/repositories/<repository_id>/sync --cookie "session=..."
```

Requires authentication and that you track `repository_id`; both a
nonexistent repository and one you don't track return the same 404. The HTTP
request never talks to GitHub — it only authorizes, creates a `SyncJob` row,
and enqueues it.

- **202 Accepted** — a new job was created and queued:
  ```json
  {
    "job_id": "3fae2b1a-...",
    "repository_id": 1,
    "status": "queued"
  }
  ```
- **409 Conflict** — a `queued` or `running` job already exists for this
  *canonical* repository (`{"detail": "Repository sync already in progress."}`,
  no job id revealed). Because `Repository` is a single global row shared by
  every user who tracks it, this holds regardless of which user started the
  active job — returning that other user's job id would create a job the
  caller couldn't subsequently `GET` (see Authorization below), so it's
  withheld entirely. This is enforced by a PostgreSQL partial unique index
  (`repository_id` unique `WHERE status IN ('queued', 'running')`) — the
  actual correctness boundary even under concurrent requests — with an
  application-level pre-check only for a cleaner error path.
- **503 Service Unavailable** — the job was created but Dramatiq/Redis
  couldn't be reached to enqueue it. The job row is marked `failed`
  (`safe_error_code: "enqueue_failed"`) rather than left as a phantom
  `queued` row nothing will ever consume; you can simply retry the request.
- **503 Service Unavailable** — `BACKGROUND_SYNC_ENABLED=false` on this
  deployment (`{"detail": "Background synchronization is unavailable in
  this deployment."}`). Distinct from the previous case: here nothing is
  created at all — no `SyncJob` row, no enqueue attempt, no Redis touched.
  See [Background sync capability flag](#background-sync-capability-flag)
  below.

### Background sync capability flag

```
BACKGROUND_SYNC_ENABLED=true   # default -- local dev, Docker Compose, tests
BACKGROUND_SYNC_ENABLED=false  # only a deployment with no worker provisioned
```

This is a **deployment-capability flag, not an environment flag** —
`APP_ENV=production` does not itself disable sync; a deployment with no
Background Worker able to process jobs does. The check
(`create_sync_job()` in `app/services/sync_jobs.py`) runs *after*
`get_tracked_repository()` (so a nonexistent or untracked repository still
returns the same 404 either way — the flag is never a way to probe
repository existence) but *before* the active-job check, before any
`SyncJob` row is created, and before `enqueue()` is ever called. Everything
else — GitHub OAuth login, `/auth/me`, tracking a repository, listing
tracked repositories, `GET /me/sync-jobs/{job_id}` for previously-created
jobs, and `GET /me/repositories/{id}/metrics` — is completely unaffected;
only *starting a new* sync is gated.

- **Full local architecture** (Docker Compose, both Option A and Option B):
  API → Redis → Dramatiq worker → GitHub → PostgreSQL, exactly as Milestone
  8 designed, with `BACKGROUND_SYNC_ENABLED` at its default `true`.
- **Free hosted demo**: intentionally sets `BACKGROUND_SYNC_ENABLED=false`,
  because the free tier this demo runs on has no persistent Background
  Worker provisioned (Render's free tier has no Background Worker instance
  type). This is a **deployment limitation, not a missing or unfinished
  feature** — the full worker/queue architecture remains implemented and
  tested exactly as before; provisioning a worker and setting the flag back
  to `true` restores production sync with no code or architecture change.

### Polling a sync job

```bash
curl http://127.0.0.1:8000/me/sync-jobs/<job_id> --cookie "session=..."
```

```json
{
  "job_id": "3fae2b1a-...",
  "repository_id": 1,
  "status": "succeeded",
  "pull_requests_processed": 25,
  "reviews_processed": 61,
  "created_at": "...",
  "started_at": "...",
  "finished_at": "...",
  "safe_error_code": null,
  "safe_error_message": null
}
```

Job states: `queued` → `running` → `succeeded` or `failed` (both terminal).
There is no `cancelled` state. `safe_error_code`/`safe_error_message` are
`null` unless `status == "failed"`, and are always drawn from a small fixed
vocabulary (e.g. `github_rate_limited`, `github_timeout`,
`github_unavailable`, `database_error`, `unexpected_error`) — never a raw
exception message or traceback.

**Authorization**: only the job's owner may `GET` it — a nonexistent job id
and one owned by another user both return the same 404, the same
never-reveal-existence pattern used for repositories. Job ids are UUIDs
(not sequential integers) specifically because they're client-facing and
cross the queue boundary; this deters casual enumeration, but the ownership
check is what actually prevents unauthorized access.

### Background architecture

```
POST /sync  ->  authorize + create SyncJob(queued)  ->  enqueue job id (string)  ->  202
                                                              |
                                                          Redis queue
                                                              |
                                                     Dramatiq worker process
                                                              |
                              load SyncJob by id -> GitHub fetch -> persist -> mark succeeded/failed
```

- **Queue**: Dramatiq + Redis. Dramatiq's `AsyncIO` middleware runs actors as
  native `async def` functions on a dedicated event-loop thread, so the
  worker reuses the same async ingestion/httpx/SQLAlchemy code as the rest of
  the app with no `asyncio.run()` bridging. Redis is used purely as the queue
  transport — no Dramatiq results backend is configured; job status lives
  entirely in PostgreSQL (`app/models/sync_job.py`).
- **No retries**: the actor is registered with `max_retries=0` — one queue
  delivery is one sync attempt. `run_sync_job` already catches every
  application failure and marks the job `failed`; a failed sync is retried
  by the user issuing a new `POST /sync` (safe, since ingestion is
  idempotent). A narrowly-scoped retry policy may be added in a later
  milestone.
- **Queue boundary**: only the job id, as a plain string (`str(job.id)`), ever
  crosses into Redis — never a `User`/`Repository` ORM object, a database
  session, or any credential. The worker (`app/worker/tasks.py`) reconstructs
  everything else from PostgreSQL by that id alone; it never performs
  authorization itself (that already happened once, synchronously, in the
  HTTP layer via `get_tracked_repository`) and has no browser session or
  OAuth token available to it, by construction.
- **GitHub auth in the worker**: identical to the rest of the app — the
  configured `GITHUB_TOKEN`, if any, otherwise unauthenticated GitHub access.
  Private repositories remain out of scope.
- **Database sessions**: the worker never holds one long-lived session. Each
  phase (mark the job `running`, perform ingestion, mark the job
  `succeeded`/`failed`) opens and closes its own short-lived
  `AsyncSession` from the same `async_session_factory` the rest of the app
  uses — nothing is held open across the (potentially many, slow) GitHub
  HTTP calls in between.
- **HTTP client**: one `httpx.AsyncClient` per sync job, opened with `async
  with` and closed when the job finishes — simple and self-cleaning, and
  avoids introducing custom Dramatiq lifecycle middleware just to pool
  connections across jobs. Worth revisiting only if real throughput demands
  it.

### Running it

See [Running the application](#running-the-application) for both supported
workflows (full Docker, or local processes against Dockerized Postgres/Redis)
— both run the exact same `dramatiq app.worker.tasks --processes 1 --threads 1`
command.

One process/one thread is a deliberate Milestone 8 choice, not an
architectural limit: PostgreSQL's partial unique index is what actually
enforces "at most one active sync per repository," not the worker's
concurrency, so running more processes/threads later is safe. One
process/thread just keeps GitHub rate-limit usage, and manual observation of
what's happening, predictable while there's exactly one background job type
and no need to demonstrate throughput yet. `--watch` is Windows-unsupported
and not used.

### Known limitations

- **Stuck `running` jobs**: if the worker process dies between marking a job
  `running` and marking it `succeeded`/`failed`, the row stays `running`
  indefinitely — nothing currently detects this. Because of the active-job
  partial unique index, a stuck `running` job also blocks any future sync
  for that repository until the row is manually updated. No heartbeat or
  stale-job sweep exists yet; this is an accepted, documented gap, not an
  oversight.
- **Lost `job_id`**: if a client never receives its `202` response (e.g. it
  disconnects mid-request), the job is still safely queued and will still
  run, but there is currently no endpoint to list a user's jobs to recover
  the id.
- **Redis durability**: the local `redis:7-alpine` Compose service has no
  AOF/RDB persistence configured. A worker restart loses nothing (Redis
  itself is untouched), but a Redis *container* restart can drop any
  queued-but-unconsumed job.
- **No retries, no cancellation, no progress percentage** — all explicitly
  out of scope for this milestone.

## Repository engineering metrics

```bash
curl http://127.0.0.1:8000/me/repositories/<repository_id>/metrics --cookie "session=..."
```

Requires authentication and that you track `repository_id` (same 404
semantics as sync — nonexistent and untracked are indistinguishable). Reads
**only** from PostgreSQL — this endpoint never calls GitHub, and has no
`GitHubClient` dependency at all, so a repository that's never been synced
still returns a valid `200` with all-empty metrics rather than an error.

```json
{
  "repository_id": 4,
  "full_name": "owner/repo",
  "total_pull_requests": 25,
  "merged_pull_requests": 18,
  "merge_rate": 0.72,
  "median_pr_cycle_time_hours": 19.4,
  "median_time_to_first_review_hours": 3.7
}
```

**Important — this describes locally ingested history, not the repository's
complete GitHub lifetime history.** `total_pull_requests` is a count of
`PullRequest` rows already stored, bounded by whatever your syncs have
captured. Because each sync fetches the *newest* 25 PRs but never deletes
older captured rows, this number isn't a fixed "≤25" cap either — it reflects
the union of whatever windows your sync history has covered over time.

Definitions:
- `total_pull_requests` — `COUNT(*)` of stored `PullRequest` rows for the repository.
- `merged_pull_requests` — `COUNT(*) WHERE merged_at IS NOT NULL`. Never inferred from `state`.
- `merge_rate` — `merged_pull_requests / total_pull_requests`. **`null`** when
  `total_pull_requests == 0` (undefined — no data at all), but a real
  **`0.0`** when PRs exist and none are merged (a genuine zero, not "unknown").
- `median_pr_cycle_time_hours` — median of `(merged_at - github_created_at)`
  in fractional hours, across merged PRs only. `statistics.median`, not mean —
  cycle times are typically right-skewed by a handful of long-lived outliers,
  and the median better represents the *typical* PR than the mean would.
  `null` when there are no merged PRs to compute over. A PR whose `merged_at`
  somehow precedes its `github_created_at` (malformed/historical data) is
  excluded from this calculation rather than contributing a negative duration.
- `median_time_to_first_review_hours` — for each PR, its earliest *qualifying*
  review's `submitted_at` minus `github_created_at`; then the median of that
  value across PRs that have at least one qualifying review. A review
  qualifies if `submitted_at IS NOT NULL` (this alone excludes PENDING
  reviews, which GitHub never gives a `submitted_at`); APPROVED,
  CHANGES_REQUESTED, COMMENTED, and DISMISSED all qualify — a dismissed
  review still represents genuine reviewer engagement at the time it was
  submitted. A review whose `submitted_at` precedes the PR's
  `github_created_at` is excluded as malformed. This metric is **independent
  of merge status** — an open, never-merged PR with a review still
  contributes. `null` when no PR has a qualifying review.

All duration/rate values are rounded to 2 decimal places at the API response
boundary; the service computes and the tests assert full precision
internally. Nothing is cached, persisted, or precomputed — every request
recalculates from `pull_requests`/`pull_request_reviews` directly, which is
entirely adequate at this scale (ingestion is capped at 25 PRs per sync).

## Authentication (Sign in with GitHub)

GitHub OAuth is the only login mechanism — no passwords. The flow uses PKCE
(RFC 7636, S256), which GitHub's OAuth docs recommend even for confidential
clients:

```
GET /auth/github/login
  -> generates state + a PKCE code_verifier (kept only in an HttpOnly cookie)
  -> 302 to GitHub with code_challenge=SHA256(code_verifier), method=S256
  -> user approves
  -> GET /auth/github/callback?code=...&state=...
  -> code_verifier from the cookie is sent alongside the code on token exchange
  -> 302 to /, with a session cookie set
```

Sessions are server-side: the browser only ever holds a random opaque token
in an `HttpOnly` cookie. The token itself is never stored — only its SHA-256
hash is persisted in Postgres (`app/core/security.py`), so a database leak
alone cannot be used to forge or replay a session. There is no `SESSION_SECRET`
because there is nothing to sign — the token's entropy is the whole security
property. The PKCE `code_verifier` follows the same rule: it lives only in a
short-lived `HttpOnly` cookie, is never persisted or logged, and is cleared
(along with `oauth_state`) as soon as the callback finishes.

### One-time setup

Create a GitHub OAuth App at https://github.com/settings/developers ("New
OAuth App", not a GitHub App):

- Homepage URL: `http://127.0.0.1:8000`
- Authorization callback URL: `http://127.0.0.1:8000/auth/github/callback`
  (must match `GITHUB_OAUTH_CALLBACK_URL` in `.env` exactly)

Copy the generated Client ID and Client Secret into your local (gitignored)
`.env`:

```
GITHUB_OAUTH_CLIENT_ID=...
GITHUB_OAUTH_CLIENT_SECRET=...
```

### Endpoints

| Endpoint | Auth | Behavior |
|---|---|---|
| `GET /auth/github/login` | No | 302 to GitHub; sets a short-lived `oauth_state` cookie |
| `GET /auth/github/callback` | No | Validates state, exchanges code, upserts the `User`, creates a `Session`, sets the `session` cookie, 302 to `/` (the frontend dashboard, once built — see [Frontend](#frontend)) |
| `GET /auth/me` | Yes | Returns the current user; 401 if not authenticated |
| `POST /auth/logout` | No (idempotent) | Deletes the session server-side and clears the cookie; succeeds even if already logged out |

If GitHub authorization is denied (`error=access_denied`), the callback
returns `200 {"detail": "GitHub authorization was cancelled."}` rather than
an error — declining sign-in is not a server failure.

## Frontend

`frontend/` is a React + TypeScript SPA (Vite, React Router, CSS Modules,
Vitest + React Testing Library — no Redux/Zustand/TanStack Query, no axios,
no UI framework, no charting library). It talks to the backend with plain
`fetch` against relative URLs and the existing `HttpOnly` session cookie —
there is no separate frontend auth token, and nothing is ever read from or
written to `localStorage`/`sessionStorage` for authentication.

**Production is a single origin.** FastAPI serves the built SPA at `/` and
its static assets at `/assets/*` (see [SPA serving](#spa-serving) below);
`/auth/*`, `/me/*`, `/health`, `/ready`, `/docs`, `/redoc`, and
`/openapi.json` are unchanged, served by the same process on the same port.
There is no second frontend origin and no CORS configuration anywhere in the
application — same-origin `fetch` and cookies work without it.

### Local frontend development

Node/npm are only needed for frontend work; the backend workflows above are
completely unaffected. Two terminals, alongside `db`/`redis`/`api` from
[Option B](#option-b-local-development):

```bash
cd frontend
npm ci          # or `npm install` the first time you add a dependency
npm run dev     # Vite dev server at http://127.0.0.1:5173
```

Vite is configured to bind explicitly to `127.0.0.1:5173` and proxy
`/auth`, `/me`, `/health`, `/ready`, and `/repositories` to
`http://127.0.0.1:8000` (`frontend/vite.config.ts`), so the app behaves as
same-origin during local development too, without needing CORS.

**Local OAuth callback, while doing frontend development:** GitHub's OAuth
redirect always lands the browser on the backend port, not Vite's — so
sign-in only works end-to-end through Vite if the GitHub OAuth App's
callback URL is temporarily pointed at the Vite origin. Update your local
(dev-only) GitHub OAuth App's Authorization callback URL to
`http://127.0.0.1:5173/auth/github/callback`, and set the matching value in
your local `.env`:

```
GITHUB_OAUTH_CALLBACK_URL=http://127.0.0.1:5173/auth/github/callback
```

then visit `http://127.0.0.1:5173` and sign in there instead of port 8000.
Switch both back to `http://127.0.0.1:8000/auth/github/callback` when you're
only working on the backend and not running Vite. **The production GitHub
OAuth App is untouched by this** — it keeps its existing
`https://github-engineering-analytics.onrender.com/auth/github/callback`
callback regardless of local frontend work.

```bash
npm run build   # tsc project build + vite build -> frontend/dist
npm test        # vitest run -- one-shot, non-watch, CI-safe
```

### SPA serving

`app/frontend.py`'s `configure_frontend()` is called last in `app/main.py`,
after every API router is already registered — Starlette matches routes in
registration order, so `/auth/*`, `/me/*`, `/health`, `/ready`, `/docs`,
`/redoc`, `/openapi.json`, and `GET /repositories` all keep precedence over
the SPA automatically, with no special-casing beyond one small reserved-path
set used only for *unmatched* paths (see below). It checks whether
`frontend/dist/index.html` exists:

- **If not** (a backend-only checkout, or the `quality` CI job, which never
  runs `npm run build`) — nothing is registered at all. No static routes, no
  catch-all, no startup failure; the backend behaves exactly as it did
  before Milestone 11.
- **If so** — `/assets` is mounted as static files, and a catch-all route
  serves `frontend/dist/index.html` for anything else, which is what makes
  client-side deep links like `/repositories/3` work: the browser requests
  that path from the server, gets the SPA shell back, and React Router takes
  over from there.

An unmatched path under a reserved backend prefix (`auth`, `me`, `health`,
`ready`, `docs`, `redoc`, `openapi.json`, `assets`) still returns a real
backend `404` rather than the SPA shell — e.g. `GET /me/not-a-real-route`.
`repositories` is deliberately *not* in that reserved set: the bare
`GET /repositories` route already wins by ordinary registration-order
precedence, while `/repositories/3` is the frontend's own route and must
fall through to the SPA shell.

### Pages and behavior

- **`/`** — the dashboard (username, tracked repositories, track-repository
  form, logout) if signed in; the landing page (a real `<a
  href="/auth/github/login">` link — never fetched via JS) otherwise.
- **`/repositories/:id`** — bookmarkable repository detail: total/merged
  pull requests, merge rate as a percentage, median cycle time and median
  time-to-first-review in human-readable units (minutes/hours/days), a
  "Not enough data" label instead of any raw `null`/`NaN`, and a sync panel.
- **Sync**: `POST .../sync` captures the returned `job_id` and polls
  `GET /me/sync-jobs/{job_id}` every 2 seconds until `succeeded`/`failed` or
  ~2 minutes elapse, then refetches metrics on success. A `409` (already
  running) and the two distinct `503`s are all shown as plain informational
  text, never as a broken-app error state. On the hosted demo specifically
  (`BACKGROUND_SYNC_ENABLED=false`), the backend's `503` is shown as "Live
  synchronization is disabled on the hosted demo. The full background worker
  architecture is implemented and runs in local Docker Compose." — the
  frontend has no built-in knowledge of that flag; it only ever reacts to
  what the HTTP response actually says.
- **Untrack** uses `DELETE /me/repositories/{id}` behind a simple inline
  confirmation; it never touches the canonical, globally-shared `Repository`
  row, only the current user's tracking relationship.

### Frontend tests

```bash
cd frontend
npm test
```

Vitest + React Testing Library only (no Playwright/Cypress/MSW). `fetch` is
mocked per-test with a small declarative helper
(`frontend/src/testUtils.ts`) rather than a network-mocking library.
Covers: unauthenticated landing vs. authenticated dashboard, the tracked
repository list, metrics rendering (including the all-null case), tracking
a repository (success and error), untracking with confirmation, logout, the
full sync polling cycle (queued → running → succeeded → refetch), a failed
sync, a `409` conflict, and the hosted-demo `503` copy.

### Docker and CI

The Dockerfile's frontend build stage (`node:24.20.0-slim`) runs `npm ci`
and `npm run build` from source and is discarded after `COPY --from` pulls
only `frontend/dist` into the final Python runtime image — the shipped image
contains no Node runtime, no npm, and no `node_modules`. `frontend/dist` and
`frontend/node_modules` are excluded from the Docker build context via
`.dockerignore`, so a stale local build is never what ends up in the image;
every image build compiles the frontend fresh. `.github/workflows/ci.yml`
adds a `frontend` job (Node 24.20.0, `npm ci` / `npm run build` / `npm
test`, no backend services) alongside `quality`; `docker-smoke` now depends
on both and additionally verifies that `GET /` returns an HTML SPA shell
from the running container.

## Running tests

Tests run against `github_analytics_test`, never the development database.
A safety check in `tests/conftest.py` refuses to run if `DATABASE_URL` does
not point at a database whose name ends in `_test`:

```bash
# PowerShell
$env:DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/github_analytics_test"
pytest

# bash
DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/github_analytics_test" pytest
```

Each test runs inside a database transaction that is rolled back afterward,
so tests never leave data behind and can run in any order. All GitHub calls
in the automated suite — REST lookups and OAuth (token exchange, user lookup)
alike — are mocked with `httpx.MockTransport`; pytest never makes a real
network call and never needs real OAuth App credentials.

### Manual verification against real GitHub

Not part of pytest. Requires a GitHub OAuth App configured as described above.
Run this against the **development** database (`github_analytics`), not the
test database:

```bash
docker compose up -d db redis
alembic upgrade head
uvicorn app.main:app --reload
dramatiq app.worker.tasks --processes 1 --threads 1   # separate terminal, needed for the sync check below
```

Tracking:
```bash
curl -X POST http://127.0.0.1:8000/me/repositories --cookie "session=..." \
  -d '{"full_name": "fastapi/fastapi"}'
curl -X POST http://127.0.0.1:8000/me/repositories --cookie "session=..." \
  -d '{"full_name": "psf/requests"}'
curl http://127.0.0.1:8000/me/repositories --cookie "session=..."
curl http://127.0.0.1:8000/repositories
docker compose exec db psql -U postgres -d github_analytics -c "select * from user_repositories;"
```
Verify: both POSTs return 201 with real numeric `github_id`s and GitHub's
canonical `full_name`s; both appear in `GET /me/repositories` with
`tracked_at`; both appear in the public `GET /repositories`; `user_repositories`
has two rows for your user. Repeating one POST returns 409 with no duplicate
row. `DELETE /me/repositories/<id>` for one returns 204, its
`user_repositories` row is gone, and the `Repository` row still appears in
`GET /repositories`; repeating the same `DELETE` returns 204 again.

OAuth login (needs a real browser — GitHub's consent page is interactive):
1. Visit `http://127.0.0.1:8000/auth/github/login`, approve on GitHub.
2. You land on `/` — the dashboard if the frontend is built (`GET /auth/me`
   under the hood), or a plain `404` if it isn't (backend-only checkout);
   either way, `curl http://127.0.0.1:8000/auth/me --cookie "session=..."`
   confirms your `github_id`/`github_login`.
3. DevTools -> Cookies -> confirm `session` is `HttpOnly`.
4. `docker exec ... psql -d github_analytics -c "select * from users; select * from sessions;"`
   -> confirm rows exist, and that no access token is stored anywhere.
5. Copy the `session` cookie value from DevTools; `curl -X POST
   http://127.0.0.1:8000/auth/logout --cookie "session=<value>"` -> confirm
   the session row is gone and `/auth/me` now returns 401.

Sync (pick a **small, quiet** repository first, not `fastapi/fastapi` —
even bounded to 25 PRs, a very active repo's most recent PRs likely have
many reviews each, making a first smoke test slower than necessary):
```bash
curl -X POST http://127.0.0.1:8000/me/repositories/<id>/sync --cookie "session=..."
# -> 202 {"job_id": "...", "repository_id": <id>, "status": "queued"}
curl http://127.0.0.1:8000/me/sync-jobs/<job_id> --cookie "session=..."
# poll until "status": "succeeded" (or "failed") -- see the worker terminal's logs meanwhile
docker compose exec db psql -U postgres -d github_analytics -c "select count(*) from pull_requests where repository_id = <id>;"
docker compose exec db psql -U postgres -d github_analytics -c "select count(*) from pull_request_reviews;"
```
Record both counts, then run the identical sync again (a fresh `POST` once
the first job is terminal) and re-run both `count(*)` queries — they must be
unchanged (the completed job's *processed* counts may look similar both
times too, but that's not the proof; the unchanged row counts are).

## Linting, formatting, and type checking

```bash
ruff check .
ruff format .
mypy app
```

## Continuous Integration

`.github/workflows/ci.yml` runs on every pull request and every push to
`main`, as three jobs:

- **`quality`** — a real PostgreSQL 16 service container (`github_analytics_test`
  only), then `alembic upgrade head`, `alembic check`, a light
  `alembic downgrade -1` / `alembic upgrade head` reversibility smoke test,
  `pytest`, `ruff check .`, `ruff format --check .`, `mypy app`. No Redis
  service — the test suite is deliberately designed not to need one (see
  [Syncing pull requests and reviews](#syncing-pull-requests-and-reviews-background)).
- **`frontend`** — Node 24.20.0 (`actions/setup-node`, npm-cached against
  `frontend/package-lock.json`), then `npm ci`, `npm run build` (includes
  TypeScript type checking), `npm test` (Vitest, non-watch). No backend
  services — every frontend test mocks `fetch` (see
  [Frontend tests](#frontend-tests)), so nothing here talks to Postgres,
  Redis, or GitHub.
- **`docker-smoke`** (`needs: [quality, frontend]`) — builds the application
  image (including the Node-based frontend build stage), brings up the
  *full* `docker compose` stack against a CI-only `.env` (derived from
  `.env.example`, with placeholder OAuth values — this job never calls
  GitHub and never performs an OAuth login), polls `/health` until it's up,
  then verifies `GET /` returns the built HTML SPA shell, that `migrate`
  exited `0`, that `worker` is still running, and that the Dramatiq actor
  imports/registers correctly (`docker run --rm <image> python -c "from
  app.worker.tasks import sync_repository_actor"` — no live Redis needed for
  that). Always tears down with `docker compose down -v`, even on failure.

`quality` and `docker-smoke` are separate jobs specifically so
`docker-smoke`'s own `db` service (started by Compose, mapped to host port
5432) never runs alongside `quality`'s GitHub Actions Postgres service
container (also on port 5432) — combining them in one job would conflict.
`frontend` needs no services at all, so it runs independently of both.

## Useful Docker commands

```bash
docker compose ps                    # what's running, and whether it's healthy
docker compose logs -f api           # follow API logs
docker compose logs -f worker        # follow worker logs
docker compose logs migrate          # inspect the last migration run
docker compose run --rm migrate      # apply migrations without starting the rest of the stack
docker compose exec db psql -U postgres -d github_analytics
docker compose down                  # stop and remove containers -- Postgres data (pgdata volume) persists
docker compose down -v               # stop and remove containers AND the pgdata volume -- deletes your local data
```

## Production deployment

**Production deployment target: Render.** This section documents the
prepared architecture and manual setup sequence; the application has not
been deployed yet, and there is no live URL. Local development is
unaffected either way — it remains exactly the Docker Compose workflow
described above (Option A/Option B). Production does not reuse
`compose.yaml`'s `db`/`redis` containers at all; it uses Render's own
managed services.

### Architecture

Same single application image and Dockerfile as local Docker Compose — no
Milestone 9 architecture change. Only the *runtime commands* and *where the
image runs* differ:

- **Web Service** — the same image, running `uvicorn app.main:app`. Public,
  gets an HTTPS URL from Render.
- **Background Worker** — the same image, running
  `dramatiq app.worker.tasks --processes 1 --threads 1`, unchanged from
  Milestone 8/9. No public port, no HTTP health server — deliberately, for
  the same reason as local Docker Compose (see
  [Known limitations (Docker/CI)](#known-limitations-docker-ci) above);
  independently restartable from the Web Service. **The free public demo
  does not provision this** — Render's free tier has no Background Worker
  instance type — so the demo's Web Service instead sets
  `BACKGROUND_SYNC_ENABLED=false` (see
  [Background sync capability flag](#background-sync-capability-flag)):
  `POST /me/repositories/{id}/sync` returns `503` rather than accepting a
  job nothing would ever process. A paid deployment that provisions this
  Background Worker and sets the flag back to `true` restores background
  sync with no code or architecture change.
- **Render Postgres** — durable, managed relational storage. Replaces
  `compose.yaml`'s local `db` container in production; nothing else about
  the SQLAlchemy/Alembic architecture changes.
- **Render Key Value** — Render's current Redis-compatible offering (new
  instances run Valkey internally while remaining Redis-client compatible).
  Used purely as the Dramatiq queue transport, exactly as local Redis is
  today — `REDIS_URL` and `dramatiq.brokers.redis.RedisBroker` need no code
  change. PostgreSQL remains the sole source of truth for `SyncJob` status;
  no job state moves into Key Value.

No `render.yaml` yet — this first deployment is being done manually through
Render's dashboard, deliberately, so the migration-ownership sequencing
between two independently-deployed services (API, then worker) is proven by
hand before it's codified as infrastructure-as-code. A later hardening step
may generate `render.yaml` from the proven, working configuration.

### Manual deployment sequence

Both services' **auto-deploy is initially OFF** — the first deployment and
its immediate follow-up checks are deliberate and manual, not triggered by
every push to `main`. Existing GitHub Actions CI (`quality` +
`docker-smoke`) is unchanged and must be green before a manual Render
deploy; no Render credentials or deploy step are added to GitHub Actions.

```
CI green
    ->
deploy API (Render builds the image, runs the Pre-Deploy Command)
    ->
API Pre-Deploy Command: alembic upgrade head
    ->
API deployment succeeds
    ->
verify /health and /ready
    ->
deploy worker (same image, same DB/Key Value, no migration command)
```

The API service owns migration execution exclusively, via a Render
**Pre-Deploy Command** (`alembic upgrade head`) — this requires a Web
Service tier that supports pre-deploy commands (Render's free Web Service
tier does not). `alembic upgrade head` is never placed in the Dockerfile's
`CMD`, the API's start command, the worker's start command, or a worker
pre-deploy command — the worker only ever *reads* an already-migrated
schema. This guarantees the worker's new revision never starts against a
schema the API hasn't already migrated. Automatic, fully independent
API+worker deployment (no manual ordering) would need a more explicit
orchestration or backward-compatible migration policy than this milestone
implements — not attempted here.

### Environment variables

Set via Render's environment/secrets UI, never committed to the repository:

```
APP_ENV=production
LOG_LEVEL=INFO
DB_ECHO=false

DATABASE_URL=<Render-generated host/credentials, but the SQLAlchemy+psycopg3
              dialect scheme: postgresql+psycopg://user:password@host/database>
REDIS_URL=<Render Key Value internal connection URL>

# false on the free Web Service (no Background Worker provisioned -- see
# "Background sync capability flag"); a paid deployment that adds a
# Background Worker sets this true instead.
BACKGROUND_SYNC_ENABLED=false

GITHUB_OAUTH_CALLBACK_URL=https://<render-host>/auth/github/callback
GITHUB_OAUTH_CLIENT_ID=<production OAuth App client id>
GITHUB_OAUTH_CLIENT_SECRET=<production OAuth App client secret>

GITHUB_TOKEN=<fine-grained PAT, public repositories, read-only>
```

**`DATABASE_URL` scheme**: Render's displayed internal database URL may
begin with plain `postgresql://`. This application uses async SQLAlchemy
with the psycopg3 driver, which requires the `postgresql+psycopg://`
dialect prefix — paste Render's generated host/credentials/database into
that scheme rather than the raw URL as displayed, unless direct testing at
setup time proves Render's value already includes the correct prefix. No
provider-specific URL-rewriting code is added to the application for
this — it stays a manual, environment-driven configuration step, consistent
with "no Docker/provider hostname hard-coded in source" from Milestone 9.

**Render `PORT`**: Render assigns the API's listening port dynamically via
a `PORT` environment variable (currently defaulting to `10000`). This is
provider runtime configuration, not an application `Settings` field, and is
handled entirely in the Render service's Start Command:

```
/bin/sh -c 'uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000}'
```

The Dockerfile's existing `CMD` (`--port 8000`) is unchanged for local
Docker Compose, which continues to publish port 8000 exactly as before —
this override exists only in Render's own service configuration.

### Production GitHub OAuth App

A **separate, production-only** GitHub OAuth App — the existing local dev
OAuth App (`http://127.0.0.1:8000/auth/github/callback`) is never repointed.
Exact steps (performed after the Render API hostname is known — see the
manual setup checklist below): GitHub → Settings → Developer settings →
OAuth Apps → New OAuth App; Homepage URL and Authorization callback URL
both use `https://<render-host>/...` (callback:
`https://<render-host>/auth/github/callback`); copy the generated Client ID
and generate a Client Secret; set both as Render environment variables on
the API and worker services (the worker does not perform OAuth itself, but
receives the same application settings for consistency — see
[Production GitHub token](#production-github-token) for why the worker
still needs *a* token). Never shared here or pasted into chat.

### Production GitHub token

A public-repository, read-only, **fine-grained personal access token**,
configured as `GITHUB_TOKEN` on the API service (and on a Background Worker
too, for any deployment that provisions one — both make outbound GitHub
REST calls: tracking a repository from the API, syncing from the worker) —
this avoids GitHub's unauthenticated 60-requests/hour limit. Still useful
on the free demo even with `BACKGROUND_SYNC_ENABLED=false`, since tracking
a repository and refreshing its metadata still call GitHub from the API.
`GITHUB_TOKEN` stays optional for local development, as today.

To create it (perform this yourself — do not paste the token value back):
1. https://github.com/settings/personal-access-tokens/new
2. **Resource owner**: your personal account.
3. **Repository access**: "Public Repositories (read-only)" — GitHub's
   fine-grained tokens grant this automatically; do not select "All
   repositories" or "Only select repositories," and do not add any
   repository or account permission beyond what's already implied by
   public-read access.
4. Do not grant any write, administration, or private-repository
   permission — none is needed for repository metadata, pull-request
   listing, or review listing, all of which are covered by public-read
   access alone.
5. **Expiration**: set one (e.g. 90 days) rather than "No expiration" —
   plan to rotate it before it lapses.
6. Generate, then paste the value directly into Render's environment
   variable UI for `GITHUB_TOKEN` on both services — never into this
   repository, a commit, or this chat.

### Health checks in production

- `GET /health` — Render's own platform health check (liveness). Unchanged
  from Milestone 9: dependency-free, so a transient Postgres blip doesn't
  make Render kill/restart an otherwise-healthy API instance.
- `GET /ready` — used for manual deployment-readiness verification (see the
  setup checklist), not wired into Render's own restart-triggering health
  check. Checks PostgreSQL only via `SELECT 1`; still never checks GitHub
  or Key Value, unchanged reasoning from Milestone 9.

### Cost

The production architecture requires paid resources for a durable data
configuration even without a Background Worker; verify current Render
pricing before provisioning. Concretely, at the time this was written:
Render Background Workers have no free instance type at all (part of why
the free public demo runs without one — see
[Background sync capability flag](#background-sync-capability-flag));
Pre-Deploy Commands require a paid web/private/worker service tier; a free
Render Postgres instance is not appropriate for durable portfolio data
because its lifecycle is time-limited; genuine Key Value persistence
requires a paid tier. No dollar figures are hard-coded here since Render's
pricing changes — check the Render dashboard directly before provisioning
anything.

### Known limitations

- **No Background Worker on the free demo, by design**: `BACKGROUND_SYNC_ENABLED=false`
  there, so `POST /me/repositories/{id}/sync` returns `503` rather than
  accepting a job. This is a deployment-tier limitation, not an unfinished
  feature — the full worker/queue architecture is implemented, tested, and
  runs today in local Docker Compose; see
  [Background sync capability flag](#background-sync-capability-flag).
- **Worker-crash-mid-job**: unchanged from Milestone 8/9, and only relevant
  to a deployment that *does* run a worker — if the worker process is
  restarted (crash, redeploy, OOM) while a `SyncJob` is `running`, that row
  stays `running` indefinitely; no heartbeat/stale-job recovery exists.
- **No `render.yaml` yet** — deployment is manual; a later hardening step
  may codify the proven configuration as a Blueprint.
- **Auto-deploy is off** — pushes to `main` do not currently redeploy
  either service; deploys are triggered manually from the Render dashboard.
- **No custom domain** — the production URL, once deployed, will be
  Render's provided `*.onrender.com` HTTPS hostname.

## Project structure

```
app/
├── main.py                    # FastAPI app instance, lifespan (DB engine + httpx client)
├── config.py                   # Typed settings loaded from environment
├── frontend.py                 # configure_frontend() -- serves frontend/dist if built, else a no-op
├── api/
│   ├── deps.py                  # get_db, get_github_client, get_github_oauth_client, get_current_user
│   ├── errors.py                 # Centralized domain-exception -> HTTP status mapping
│   └── routes/                   # API route modules
│       ├── health.py             # GET /health, GET /ready
│       ├── repositories.py       # GET /repositories, POST/GET/DELETE /me/repositories, /sync, /sync-jobs/{id}, /metrics
│       └── auth.py               # GET /auth/github/login, /callback, /me, POST /auth/logout
├── core/
│   ├── logging.py               # Logging configuration
│   └── security.py              # hash_token() — centralized session-token hashing
├── db/
│   ├── base.py                   # Declarative base + naming convention
│   └── session.py                # Async engine and session factory
├── github/
│   ├── client.py                # GitHubClient — repository/PR/review REST lookups + pagination
│   ├── oauth_client.py           # GitHubOAuthClient — authorize URL, token exchange, user lookup
│   ├── schemas.py                # GitHubRepository, GitHubPullRequest, GitHubReview, ...
│   └── errors.py                 # GitHubError hierarchy (incl. GitHubOAuthError)
├── models/
│   ├── repository.py           # Repository ORM model (global catalog)
│   ├── user.py                  # User ORM model (github_id authoritative)
│   ├── session.py               # Session ORM model (token_hash only, FK -> users, CASCADE)
│   ├── user_repository.py      # UserRepository — composite PK (user_id, repository_id)
│   ├── pull_request.py         # PullRequest — UNIQUE(github_id), UNIQUE(repository_id, number)
│   ├── pull_request_review.py  # PullRequestReview — UNIQUE(github_id)
│   └── sync_job.py             # SyncJob — UUID PK, status CHECK, partial unique active-job index
├── services/
│   ├── repositories.py         # resolve_repository(), get_tracked_repository(), track/untrack_repository_for_user()
│   ├── repository_sync.py      # sync_repository() — bounded PR/review ingestion, Postgres upserts
│   ├── repository_metrics.py   # get_repository_metrics() — Postgres-only, no GitHub calls
│   ├── sync_jobs.py            # create_sync_job(), get_own_sync_job(), enqueue_sync_job()
│   ├── readiness.py             # check_database_ready() — backs GET /ready, Postgres-only
│   └── auth.py                  # OAuth state, login completion, session validation/deletion
├── worker/
│   ├── broker.py                # RedisBroker + AsyncIO middleware, sets the global Dramatiq broker
│   └── tasks.py                 # run_sync_job() + sync_repository_actor (max_retries=0)
└── schemas/
    ├── health.py                # Pydantic response models
    ├── repository.py             # RepositoryResponse, RepositoryCreateRequest, TrackedRepositoryResponse
    ├── sync_job.py                # SyncJobCreatedResponse, SyncJobResponse
    ├── metrics.py                 # RepositoryMetricsResponse
    └── user.py                   # UserResponse

alembic/                    # Migration environment and versions
frontend/                   # React + TypeScript SPA (Vite, React Router, CSS Modules)
├── src/
│   ├── main.tsx / App.tsx         # Entry point + routing (/ and /repositories/:id)
│   ├── api/                        # client.ts (typed fetch wrapper), types.ts
│   ├── hooks/                      # useAuth.ts, useSyncJob.ts (polling state machine)
│   ├── pages/                      # LandingPage, DashboardPage, RepositoryDetailPage
│   ├── components/                 # Button, Card, Badge, Spinner, MetricCard, ...
│   └── styles/                     # tokens.css, global.css
├── package.json / package-lock.json
└── vite.config.ts                  # dev server on 127.0.0.1:5173, proxies to :8000
Dockerfile                  # Node build stage (frontend) + Python runtime stage, used by migrate/api/worker
.dockerignore
compose.yaml                # db, redis, migrate, api, worker
.github/workflows/ci.yml    # quality job + frontend job + docker-smoke job
tests/                      # pytest suite (incl. tests/test_frontend_serving.py)
```
