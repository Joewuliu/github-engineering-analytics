# GitHub Engineering Analytics Platform

A backend application that connects to GitHub, ingests repository activity,
and calculates engineering analytics. This project is intended to demonstrate
production-quality backend engineering practices.

## Current Status: Milestone 4 — GitHub OAuth Authentication

Milestone 1 established the base FastAPI application. Milestone 2 added
PostgreSQL, async SQLAlchemy 2.x, and Alembic migrations. Milestone 3 added
GitHub REST API integration (`POST /repositories` looks a repository up on
GitHub and persists it). Milestone 4 adds "Sign in with GitHub": a full OAuth
authorization-code flow, server-side sessions (opaque tokens, hashed before
storage), and `POST /repositories` now requires authentication.
`GET /repositories` and `GET /health` remain public. GitHub GraphQL, webhooks,
Redis, background workers, and analytics still do not exist — those arrive in
later milestones.

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

`POST /repositories` looks the repository up on the real GitHub REST API
(`https://api.github.com`) before persisting it. `GITHUB_TOKEN` is optional —
public repositories work without one. Set it in your local (gitignored) `.env`
only if you want GitHub's higher authenticated rate limit (5000 requests/hour
instead of 60); never commit a real token. A shared `httpx.AsyncClient` is
created once in the FastAPI lifespan and closed on shutdown, so repeated
requests reuse pooled connections.

### Tracking a repository (requires authentication)

```bash
curl -X POST http://127.0.0.1:8000/repositories \
  -H "Content-Type: application/json" \
  --cookie "session=<your session cookie value>" \
  -d '{"full_name": "fastapi/fastapi"}'
```

- **201 Created** — the repository was fetched from GitHub and persisted.
  The response's `full_name` is GitHub's canonical casing, which may differ
  from what you submitted.
- **401 Unauthorized** — no valid session (see Authentication below).
- **409 Conflict** — a repository with that GitHub id is already tracked
  (checked by GitHub's numeric id, not the submitted string, so resubmitting
  with different casing still resolves to the same conflict).
- **404** — GitHub has no such repository.
- **502/503/504** — GitHub (or the network) failed in some way; see
  `app/api/errors.py` for the full mapping. Responses never include GitHub
  tokens, raw upstream bodies, or stack traces.

### Listing tracked repositories (public)

```bash
curl http://127.0.0.1:8000/repositories
```

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

Repository import:
```bash
curl -X POST http://127.0.0.1:8000/repositories --cookie "session=..." \
  -d '{"full_name": "fastapi/fastapi"}'
curl http://127.0.0.1:8000/repositories
```
Verify: a 201 with a real numeric `github_id` and GitHub's canonical
`full_name`; the repository appears in `GET /repositories`; the row exists in
Postgres; repeating the same `POST` returns 409 with no duplicate row.

OAuth login (needs a real browser — GitHub's consent page is interactive):
1. Visit `http://127.0.0.1:8000/auth/github/login`, approve on GitHub.
2. You land on `/auth/me`, showing your `github_id`/`github_login`.
3. DevTools -> Cookies -> confirm `session` is `HttpOnly`.
4. `docker exec ... psql -d github_analytics -c "select * from users; select * from sessions;"`
   -> confirm rows exist, and that no access token is stored anywhere.
5. Copy the `session` cookie value from DevTools; `curl -X POST
   http://127.0.0.1:8000/auth/logout --cookie "session=<value>"` -> confirm
   the session row is gone and `/auth/me` now returns 401.

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
│       ├── repositories.py       # GET /repositories, POST /repositories (auth required)
│       └── auth.py               # GET /auth/github/login, /callback, /me, POST /auth/logout
├── core/
│   ├── logging.py               # Logging configuration
│   └── security.py              # hash_token() — centralized session-token hashing
├── db/
│   ├── base.py                   # Declarative base + naming convention
│   └── session.py                # Async engine and session factory
├── github/
│   ├── client.py                # GitHubClient — repository REST lookups
│   ├── oauth_client.py           # GitHubOAuthClient — authorize URL, token exchange, user lookup
│   ├── schemas.py                # GitHubRepository, GitHubAuthenticatedUser
│   └── errors.py                 # GitHubError hierarchy (incl. GitHubOAuthError)
├── models/
│   ├── repository.py           # Repository ORM model
│   ├── user.py                  # User ORM model (github_id authoritative)
│   └── session.py               # Session ORM model (token_hash only, FK -> users, CASCADE)
├── services/
│   ├── repository_import.py   # import_repository() — GitHub fetch + persist + dedupe
│   └── auth.py                  # OAuth state, login completion, session validation/deletion
└── schemas/
    ├── health.py                # Pydantic response models
    ├── repository.py             # RepositoryResponse, RepositoryCreateRequest
    └── user.py                   # UserResponse

alembic/                    # Migration environment and versions
compose.yaml                # PostgreSQL (dev + test databases)
tests/                      # pytest suite
```
