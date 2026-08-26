from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.github.client import GitHubClient
from app.models.repository import Repository
from app.models.user import User
from app.models.user_repository import UserRepository
from app.services.repositories import (
    RepositoryAlreadyTrackedError,
    resolve_repository,
    track_repository_for_user,
    untrack_repository_for_user,
)


def _github_client(payload: dict[str, object]) -> GitHubClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.github.com"
    )
    return GitHubClient(http_client)


async def _make_user(db_session: AsyncSession, github_id: int, login: str) -> User:
    user = User(github_id=github_id, github_login=login)
    db_session.add(user)
    await db_session.flush()
    return user


# ---- resolve_repository -------------------------------------------------------


async def test_resolve_repository_creates_new_repository(db_session: AsyncSession) -> None:
    github_client = _github_client({"id": 111, "full_name": "octocat/hello-world"})

    repository = await resolve_repository("octocat/hello-world", db_session, github_client)

    assert repository.id is not None
    assert repository.github_id == 111
    assert repository.full_name == "octocat/hello-world"


async def test_resolve_repository_stores_github_canonical_casing(db_session: AsyncSession) -> None:
    github_client = _github_client({"id": 222, "full_name": "Owner/MyRepo"})

    repository = await resolve_repository("owner/myrepo", db_session, github_client)

    assert repository.full_name == "Owner/MyRepo"


async def test_resolve_repository_reuses_existing_global_repository(
    db_session: AsyncSession,
) -> None:
    db_session.add(Repository(github_id=333, full_name="octocat/existing"))
    await db_session.commit()

    github_client = _github_client({"id": 333, "full_name": "octocat/existing"})

    repository = await resolve_repository("octocat/existing", db_session, github_client)

    result = await db_session.execute(select(Repository).where(Repository.github_id == 333))
    assert len(result.scalars().all()) == 1
    assert repository.github_id == 333


async def test_resolve_repository_recovers_from_creation_race(db_session: AsyncSession) -> None:
    # Pre-insert a row with a *different* github_id but the same full_name
    # GitHub will report for the id we're resolving. resolve_repository's
    # pre-check (keyed on github_id) won't find it, so its own insert fails
    # on the full_name unique constraint -- the same failure shape a genuine
    # concurrent "someone else just created this repository" race produces.
    db_session.add(Repository(github_id=444, full_name="octocat/race-target"))
    await db_session.commit()

    github_client = _github_client({"id": 555, "full_name": "octocat/race-target"})

    repository = await resolve_repository("some/other-input", db_session, github_client)

    assert repository.github_id == 444
    assert repository.full_name == "octocat/race-target"

    # Session must remain usable after the internal rollback.
    result = await db_session.execute(select(Repository).where(Repository.github_id == 555))
    assert result.scalar_one_or_none() is None


# ---- track_repository_for_user -------------------------------------------------


async def test_track_repository_for_user_creates_new_repository_and_tracking(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session, 1, "octocat")
    github_client = _github_client({"id": 601, "full_name": "octocat/new-repo"})

    repository = await track_repository_for_user(
        "octocat/new-repo", user, db_session, github_client
    )

    assert repository.github_id == 601
    result = await db_session.execute(
        select(UserRepository).where(
            UserRepository.user_id == user.id, UserRepository.repository_id == repository.id
        )
    )
    assert result.scalar_one() is not None


async def test_track_repository_for_user_reuses_existing_global_repository(
    db_session: AsyncSession,
) -> None:
    other_user = await _make_user(db_session, 2, "other-user")
    repository = Repository(github_id=602, full_name="octocat/shared")
    db_session.add(repository)
    await db_session.flush()
    db_session.add(UserRepository(user_id=other_user.id, repository_id=repository.id))
    await db_session.commit()

    new_user = await _make_user(db_session, 3, "new-user")
    github_client = _github_client({"id": 602, "full_name": "octocat/shared"})

    result_repo = await track_repository_for_user(
        "octocat/shared", new_user, db_session, github_client
    )

    assert result_repo.id == repository.id
    all_repos = await db_session.execute(select(Repository).where(Repository.github_id == 602))
    assert len(all_repos.scalars().all()) == 1

    trackers = await db_session.execute(
        select(UserRepository).where(UserRepository.repository_id == repository.id)
    )
    assert {t.user_id for t in trackers.scalars().all()} == {other_user.id, new_user.id}


async def test_track_repository_for_user_same_user_twice_raises_conflict(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session, 4, "octocat")
    github_client = _github_client({"id": 603, "full_name": "octocat/already-tracked"})

    await track_repository_for_user("octocat/already-tracked", user, db_session, github_client)

    with pytest.raises(RepositoryAlreadyTrackedError):
        await track_repository_for_user("octocat/already-tracked", user, db_session, github_client)


async def test_track_repository_for_user_translates_commit_race_to_conflict(
    db_session: AsyncSession,
) -> None:
    # UserRepository's only constraint is its composite primary key, so unlike
    # resolve_repository there's no secondary constraint to proxy a same-
    # session race through, and genuine cross-connection concurrency isn't
    # reachable from inside this project's savepoint-isolated test sessions.
    # Instead, force the exact failure the commit-time except block must
    # handle: patch commit() to raise IntegrityError on this one call and
    # verify it's translated to the same domain conflict, not leaked.
    user = await _make_user(db_session, 5, "octocat")
    github_client = _github_client({"id": 604, "full_name": "octocat/race"})

    with (
        patch.object(
            db_session, "commit", side_effect=IntegrityError("stmt", {}, Exception("duplicate key"))
        ),
        pytest.raises(RepositoryAlreadyTrackedError),
    ):
        await track_repository_for_user("octocat/race", user, db_session, github_client)


# ---- untrack_repository_for_user -----------------------------------------------


async def test_untrack_repository_for_user_removes_only_that_association(
    db_session: AsyncSession,
) -> None:
    user_a = await _make_user(db_session, 6, "user-a")
    user_b = await _make_user(db_session, 7, "user-b")
    repository = Repository(github_id=605, full_name="octocat/untrack-me")
    db_session.add(repository)
    await db_session.flush()
    db_session.add(UserRepository(user_id=user_a.id, repository_id=repository.id))
    db_session.add(UserRepository(user_id=user_b.id, repository_id=repository.id))
    await db_session.commit()

    await untrack_repository_for_user(repository.id, user_a, db_session)

    remaining = await db_session.execute(
        select(UserRepository).where(UserRepository.repository_id == repository.id)
    )
    assert [a.user_id for a in remaining.scalars().all()] == [user_b.id]

    repo_result = await db_session.execute(select(Repository).where(Repository.id == repository.id))
    assert repo_result.scalar_one() is not None


async def test_untrack_repository_for_user_is_a_noop_when_not_tracked(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session, 8, "octocat")

    await untrack_repository_for_user(999999, user, db_session)  # must not raise
