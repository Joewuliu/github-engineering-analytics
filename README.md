# GitHub Engineering Analytics Platform

A backend application that connects to GitHub, ingests repository activity,
and calculates engineering analytics. This project is intended to demonstrate
production-quality backend engineering practices.

## Current Status: Milestone 3 — GitHub REST API Integration

Milestone 1 established the base FastAPI application. Milestone 2 added
PostgreSQL, async SQLAlchemy 2.x, and Alembic migrations. Milestone 3 adds
real GitHub REST API integration: `POST /repositories` looks a repository up
on GitHub, treats GitHub's response as authoritative, and persists it.
`GET /repositories` continues to read from the database only. GitHub OAuth,
GraphQL, webhooks, Redis, background workers, and analytics still do not
exist — those arrive in later milestones.

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

### Tracking a repository

```bash
curl -X POST http://127.0.0.1:8000/repositories \
  -H "Content-Type: application/json" \
  -d '{"full_name": "fastapi/fastapi"}'
```

- **201 Created** — the repository was fetched from GitHub and persisted.
  The response's `full_name` is GitHub's canonical casing, which may differ
  from what you submitted.
- **409 Conflict** — a repository with that GitHub id is already tracked
  (checked by GitHub's numeric id, not the submitted string, so resubmitting
  with different casing still resolves to the same conflict).
- **404** — GitHub has no such repository.
- **502/503/504** — GitHub (or the network) failed in some way; see
  `app/api/errors.py` for the full mapping. Responses never include GitHub
  tokens, raw upstream bodies, or stack traces.

### Listing tracked repositories

```bash
curl http://127.0.0.1:8000/repositories
```

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
in the automated suite are mocked with `httpx.MockTransport` — pytest never
makes a real network call.

### Manual verification against real GitHub

Not part of pytest. Run this against the **development** database
(`github_analytics`), not the test database:

```bash
docker compose up -d db
alembic upgrade head
uvicorn app.main:app --reload

curl -X POST http://127.0.0.1:8000/repositories -d '{"full_name": "fastapi/fastapi"}'
curl http://127.0.0.1:8000/repositories
```

Verify: a 201 with a real numeric `github_id` and GitHub's canonical
`full_name`; the repository appears in `GET /repositories`; the row exists in
Postgres (`docker exec ... psql -d github_analytics -c "select * from repositories;"`);
repeating the same `POST` returns 409 with no duplicate row created.

## Linting, formatting, and type checking

```bash
ruff check .
ruff format .
mypy app
```

## Project structure

```
app/
├── main.py                  # FastAPI app instance, lifespan (DB engine + httpx client)
├── config.py                 # Typed settings loaded from environment
├── api/
│   ├── deps.py                # FastAPI dependencies (get_db, get_github_client)
│   ├── errors.py               # Centralized domain-exception -> HTTP status mapping
│   └── routes/                 # API route modules
│       ├── health.py           # GET /health
│       └── repositories.py     # GET /repositories, POST /repositories
├── core/logging.py           # Logging configuration
├── db/
│   ├── base.py                 # Declarative base + naming convention
│   └── session.py              # Async engine and session factory
├── github/
│   ├── client.py              # GitHubClient — GitHub-specific HTTP behavior
│   ├── schemas.py              # GitHubRepository — minimal response parsing
│   └── errors.py               # GitHubError hierarchy
├── models/
│   └── repository.py         # Repository ORM model
├── services/
│   └── repository_import.py  # import_repository() — GitHub fetch + persist + dedupe
└── schemas/
    ├── health.py              # Pydantic response models
    └── repository.py           # RepositoryResponse, RepositoryCreateRequest

alembic/                    # Migration environment and versions
compose.yaml                # PostgreSQL (dev + test databases)
tests/                      # pytest suite
```
