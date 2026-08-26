import logging

import httpx
from pydantic import ValidationError

from app.github.errors import (
    GitHubAuthenticationError,
    GitHubConnectionError,
    GitHubRateLimitError,
    GitHubRepositoryNotFoundError,
    GitHubResponseError,
    GitHubServerError,
    GitHubTimeoutError,
)
from app.github.schemas import GitHubPullRequest, GitHubRepository, GitHubReview

logger = logging.getLogger(__name__)


class GitHubClient:
    """Thin wrapper around the GitHub REST API for repository/PR/review lookups."""

    def __init__(self, http_client: httpx.AsyncClient, token: str | None = None) -> None:
        self._http_client = http_client
        self._token = token

    async def get_repository(self, full_name: str) -> GitHubRepository:
        response = await self._get(
            f"/repos/{full_name}",
            not_found_message=f"GitHub repository '{full_name}' not found.",
        )
        payload = self._parse_json(response)
        try:
            return GitHubRepository.model_validate(payload)
        except ValidationError as exc:
            raise GitHubResponseError("GitHub response was missing expected fields.") from exc

    async def list_pull_requests(self, full_name: str, limit: int) -> list[GitHubPullRequest]:
        """Return up to `limit` pull requests, newest first, across open/closed/merged."""
        raw_items = await self._paginate(
            f"/repos/{full_name}/pulls",
            {"state": "all", "sort": "created", "direction": "desc", "per_page": min(limit, 100)},
            limit=limit,
            not_found_message=f"GitHub repository '{full_name}' not found.",
        )
        try:
            return [GitHubPullRequest.model_validate(item) for item in raw_items]
        except ValidationError as exc:
            raise GitHubResponseError("GitHub response was missing expected fields.") from exc

    async def list_reviews(self, full_name: str, number: int) -> list[GitHubReview]:
        """Return every review for one pull request, following pagination fully."""
        raw_items = await self._paginate(
            f"/repos/{full_name}/pulls/{number}/reviews",
            {"per_page": 100},
            not_found_message=f"GitHub pull request '{full_name}#{number}' not found.",
        )
        try:
            return [GitHubReview.model_validate(item) for item in raw_items]
        except ValidationError as exc:
            raise GitHubResponseError("GitHub response was missing expected fields.") from exc

    async def _paginate(
        self,
        path: str,
        params: dict[str, str | int],
        *,
        limit: int | None = None,
        not_found_message: str | None = None,
    ) -> list[object]:
        results: list[object] = []
        next_url: str | None = path
        next_params: dict[str, str | int] | None = params

        while next_url:
            response = await self._get(
                next_url, params=next_params, not_found_message=not_found_message
            )
            page = self._parse_json(response)
            if not isinstance(page, list):
                raise GitHubResponseError("GitHub returned a malformed response.")
            results.extend(page)

            if limit is not None and len(results) >= limit:
                return results[:limit]

            next_link = response.links.get("next")
            next_url = next_link["url"] if next_link else None
            next_params = None  # the Link header URL already carries all query params

        return results

    async def _get(
        self,
        url: str,
        params: dict[str, str | int] | None = None,
        *,
        not_found_message: str | None = None,
    ) -> httpx.Response:
        try:
            response = await self._http_client.get(url, params=params, headers=self._headers())
        except httpx.TimeoutException as exc:
            raise GitHubTimeoutError("Timed out contacting GitHub.") from exc
        except httpx.ConnectError as exc:
            raise GitHubConnectionError("Could not connect to GitHub.") from exc
        except httpx.HTTPError as exc:
            raise GitHubConnectionError("Network error while contacting GitHub.") from exc

        self._log_rate_limit(response)
        self._raise_for_status(response, not_found_message=not_found_message)
        return response

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    @staticmethod
    def _raise_for_status(response: httpx.Response, *, not_found_message: str | None) -> None:
        if response.status_code == 404:
            raise GitHubRepositoryNotFoundError(not_found_message or "GitHub resource not found.")
        if response.status_code == 401:
            raise GitHubAuthenticationError("GitHub rejected our credentials.")
        if response.status_code == 403 and GitHubClient._is_rate_limited(response):
            raise GitHubRateLimitError("GitHub API rate limit exceeded.")
        if response.status_code >= 500:
            raise GitHubServerError("GitHub returned a server error.")
        if response.status_code >= 400:
            raise GitHubResponseError("GitHub rejected the request.")

    @staticmethod
    def _parse_json(response: httpx.Response) -> object:
        try:
            return response.json()
        except ValueError as exc:
            raise GitHubResponseError("GitHub returned a malformed response.") from exc

    @staticmethod
    def _is_rate_limited(response: httpx.Response) -> bool:
        if response.headers.get("x-ratelimit-remaining") == "0":
            return True
        return "retry-after" in response.headers

    @staticmethod
    def _log_rate_limit(response: httpx.Response) -> None:
        remaining = response.headers.get("x-ratelimit-remaining")
        if remaining is not None:
            logger.debug(
                "GitHub rate limit: remaining=%s reset=%s",
                remaining,
                response.headers.get("x-ratelimit-reset"),
            )
