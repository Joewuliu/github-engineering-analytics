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

Milestones 1–6 are complete:

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

Do not implement additional OAuth providers, password authentication, GitHub
GraphQL, GitHub webhooks, commit ingestion, issue ingestion, contributors,
organizations, analytics, Redis, caching, background workers, retry
infrastructure, CI/CD, deployment infrastructure, role-based authorization,
or an admin system until their respective milestones.