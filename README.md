# GitHub Engineering Analytics Platform

A backend application that connects to GitHub, ingests repository activity,
and calculates engineering analytics. This project is intended to demonstrate
production-quality backend engineering practices.

## Current Status: Milestone 2 — Persistence / Database Foundation

Milestone 1 established the base FastAPI application. Milestone 2 adds
PostgreSQL, async SQLAlchemy 2.x, and Alembic migrations, plus a single
`Repository` model and a `GET /repositories` endpoint that reads from the
database end-to-end. GitHub integration, OAuth, Redis, background workers,
and analytics still do not exist — those arrive in later milestones.

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
so tests never leave data behind and can run in any order.

## Linting, formatting, and type checking

```bash
ruff check .
ruff format .
mypy app
```

## Project structure

```
app/
├── main.py                # FastAPI app instance and lifespan wiring
├── config.py               # Typed settings loaded from environment
├── api/
│   ├── deps.py              # FastAPI dependencies (e.g. get_db)
│   └── routes/               # API route modules
│       ├── health.py         # GET /health
│       └── repositories.py   # GET /repositories
├── core/logging.py         # Logging configuration
├── db/
│   ├── base.py               # Declarative base + naming convention
│   └── session.py            # Async engine and session factory
├── models/
│   └── repository.py       # Repository ORM model
└── schemas/
    ├── health.py            # Pydantic response models
    └── repository.py

alembic/                    # Migration environment and versions
compose.yaml                # PostgreSQL (dev + test databases)
tests/                      # pytest suite
```
