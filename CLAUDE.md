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

Milestones 1–3 are complete:

- Milestone 1: FastAPI backend foundation.
- Milestone 2: PostgreSQL via Docker Compose, async SQLAlchemy 2.x, Alembic
  migrations, a `Repository` model, `GET /repositories`.
- Milestone 3: GitHub REST API integration. `POST /repositories` validates
  input, looks the repository up on the real GitHub REST API via a
  lifespan-owned shared `httpx.AsyncClient`, treats GitHub's response as
  authoritative, persists it, and returns 201; duplicate imports (by GitHub's
  numeric id) return 409. GitHub failures are mapped to safe, explicit HTTP
  statuses (404/502/503/504) with no tokens, stack traces, or raw upstream
  bodies exposed. `httpx` is a runtime dependency.

Do not implement GitHub OAuth, GitHub GraphQL, GitHub webhooks, commits,
pull requests, reviews, issues, contributors, organizations, analytics,
Redis, caching, background workers, retry infrastructure, CI/CD, or
deployment infrastructure until their respective milestones.