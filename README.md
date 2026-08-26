# GitHub Engineering Analytics Platform

A backend application that connects to GitHub, ingests repository activity,
and calculates engineering analytics. This project is intended to demonstrate
production-quality backend engineering practices.

## Current Status: Milestone 7 — Repository Engineering Analytics

Milestone 1 established the base FastAPI application. Milestone 2 added
PostgreSQL, async SQLAlchemy 2.x, and Alembic migrations. Milestone 3 added
GitHub REST API integration. Milestone 4 added "Sign in with GitHub" (OAuth
with PKCE, server-side sessions). Milestone 5 introduced per-user repository
tracking. Milestone 6 ingested pull requests and reviews via
`POST /me/repositories/{repository_id}/sync`. Milestone 7 turns that stored
data into the first engineering metrics via
`GET /me/repositories/{repository_id}/metrics` — computed entirely from
PostgreSQL, no GitHub calls. GitHub GraphQL, webhooks, Redis, background
workers, and further analytics (trends, contributor/team breakdowns) still do
not exist — those arrive in later milestones.

## Requirements

- Python 3.12+
- Docker and Docker Compose (for PostgreSQL)

## Setup

Create and activate a virtual environment, then install the project with dev
dependencies:

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

## Database (PostgreSQL via Docker Compose)

`compose.yaml` runs a single PostgreSQL container. On first start it creates
two databases: `github_analytics` (development) and `github_analytics_test`
(used only by the test suite), via `docker/postgres/init-test-db.sql`.

Start it:

```bash
docker compose up -d db
```

Stop it (data persists in the `pgdata` volume):

```bash
docker compose stop db
```

Stop and remove it, including all data:

```bash
docker compose down -v
```

### Migrations

Alembic reads the database URL from `Settings` (`app/config.py`), not from
`alembic.ini`, so `.env`/`DATABASE_URL` is the single source of truth.

```bash
alembic upgrade head              # apply all migrations
alembic revision --autogenerate -m "description"   # generate a new migration
```

## Running the server

```bash
uvicorn app.main:app --reload
```

Note for Windows: this works as-is because `--reload` makes uvicorn run the
server in a subprocess, and uvicorn selects `SelectorEventLoop` for that case
— which is what psycopg's async driver requires. Running *without* `--reload`
(e.g. a future single-process production command) would hit Windows's default
`ProactorEventLoop`, which psycopg cannot use; if that's ever needed, pass
`--loop asyncio:SelectorEventLoop` explicitly at that call site.

The API will be available at http://127.0.0.1:8000, with interactive docs at
http://127.0.0.1:8000/docs and the OpenAPI schema at
http://127.0.0.1:8000/openapi.json.

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
| `POST /me/repositories/{repository_id}/sync` | Yes | Ingest PRs/reviews for a tracked repository (see below) |
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

## Syncing pull requests and reviews

```bash
curl -X POST http://127.0.0.1:8000/me/repositories/<repository_id>/sync --cookie "session=..."
```

Fetches this repository's pull requests from GitHub (`state=all`, newest
first) and every fetched PR's reviews, then persists them. Requires
authentication and that you track `repository_id`; both a nonexistent
repository and one you don't track return the same 404, so the endpoint never
reveals whether an untracked repository exists globally.

**Bounded to the most recent `MAX_PULL_REQUESTS_PER_SYNC` = 25 pull requests**
(`app/services/repository_sync.py`) — deliberately small and easy to find/
replace later. Each PR needs its own separate GitHub "list reviews" request,
so a larger limit multiplies outbound API calls quickly; 25 keeps a sync
usable even without `GITHUB_TOKEN` configured (GitHub's unauthenticated limit
is 60 requests/hour). Real full-history or incremental sync is deferred to
whenever background workers are introduced — this milestone is a bounded,
synchronous demonstration.

All GitHub fetching (the PR list and every PR's reviews) completes and is
held in memory *before* any database write begins; persistence then happens
as a single all-or-nothing transaction — a failure at any point during
GitHub fetching leaves no partial writes, and a database failure during
persistence rolls back the entire sync.

Response:
```json
{
  "repository_id": 1,
  "full_name": "octocat/hello-world",
  "pull_requests_processed": 25,
  "reviews_processed": 61
}
```
"Processed" means examined/upserted, not newly inserted — running the same
sync twice against unchanged GitHub data produces the same counts but zero
new rows (verified by inspecting table row counts, not by the response
alone). Re-syncing updates a PR's mutable fields (`state`, `title`,
`author_login`, `closed_at`, `merged_at`, `github_updated_at`) and a review's
(`state`, `reviewer_login`, `submitted_at`) in place; identity fields
(`github_id`, `repository_id`, `number`, `pull_request_id`,
`github_created_at`) never change. Upserts use PostgreSQL's native
`INSERT ... ON CONFLICT DO UPDATE` (via `sqlalchemy.dialects.postgresql.insert`),
not a select-then-branch pattern — this is a deliberate, local exception to
the pattern used elsewhere in the codebase, chosen for this ingestion
workload's idempotency and round-trip-count needs.

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
  -> 302 to /auth/me, with a session cookie set
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
| `GET /auth/github/callback` | No | Validates state, exchanges code, upserts the `User`, creates a `Session`, sets the `session` cookie, 302 to `/auth/me` |
| `GET /auth/me` | Yes | Returns the current user; 401 if not authenticated |
| `POST /auth/logout` | No (idempotent) | Deletes the session server-side and clears the cookie; succeeds even if already logged out |

If GitHub authorization is denied (`error=access_denied`), the callback
returns `200 {"detail": "GitHub authorization was cancelled."}` rather than
an error — declining sign-in is not a server failure.

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
docker compose up -d db
alembic upgrade head
uvicorn app.main:app --reload
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
2. You land on `/auth/me`, showing your `github_id`/`github_login`.
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
docker compose exec db psql -U postgres -d github_analytics -c "select count(*) from pull_requests where repository_id = <id>;"
docker compose exec db psql -U postgres -d github_analytics -c "select count(*) from pull_request_reviews;"
```
Record both counts, then run the identical sync again and re-run both
`count(*)` queries — they must be unchanged (the response's *processed*
counts may look similar both times too, but that's not the proof; the
unchanged row counts are).

## Linting, formatting, and type checking

```bash
ruff check .
ruff format .
mypy app
```

## Project structure

```
app/
├── main.py                    # FastAPI app instance, lifespan (DB engine + httpx client)
├── config.py                   # Typed settings loaded from environment
├── api/
│   ├── deps.py                  # get_db, get_github_client, get_github_oauth_client, get_current_user
│   ├── errors.py                 # Centralized domain-exception -> HTTP status mapping
│   └── routes/                   # API route modules
│       ├── health.py             # GET /health
│       ├── repositories.py       # GET /repositories, POST/GET/DELETE /me/repositories, /sync, /metrics
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
│   └── pull_request_review.py  # PullRequestReview — UNIQUE(github_id)
├── services/
│   ├── repositories.py         # resolve_repository(), get_tracked_repository(), track/untrack_repository_for_user()
│   ├── repository_sync.py      # sync_repository() — bounded PR/review ingestion, Postgres upserts
│   ├── repository_metrics.py   # get_repository_metrics() — Postgres-only, no GitHub calls
│   └── auth.py                  # OAuth state, login completion, session validation/deletion
└── schemas/
    ├── health.py                # Pydantic response models
    ├── repository.py             # RepositoryResponse, RepositoryCreateRequest, TrackedRepositoryResponse
    ├── sync.py                   # RepositorySyncResponse
    ├── metrics.py                 # RepositoryMetricsResponse
    └── user.py                   # UserResponse

alembic/                    # Migration environment and versions
compose.yaml                # PostgreSQL (dev + test databases)
tests/                      # pytest suite
```
