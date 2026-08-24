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

Establish the initial FastAPI backend foundation.

Do not implement PostgreSQL, GitHub OAuth, Redis, background workers, or
deployment infrastructure until their respective milestones.