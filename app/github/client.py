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
from app.github.schemas import GitHubRepository

logger = logging.getLogger(__name__)


class GitHubClient:
    """Thin wrapper around the GitHub REST API for repository lookups."""

    def __init__(self, http_client: httpx.AsyncClient, token: str | None = None) -> None:
        self._http_client = http_client
        self._token = token

    async def get_repository(self, full_name: str) -> GitHubRepository:
        headers = {"Accept": "application/vnd.github+json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        try:
            response = await self._http_client.get(f"/repos/{full_name}", headers=headers)
        except httpx.TimeoutException as exc:
            raise GitHubTimeoutError("Timed out contacting GitHub.") from exc
        except httpx.ConnectError as exc:
            raise GitHubConnectionError("Could not connect to GitHub.") from exc
        except httpx.HTTPError as exc:
            raise GitHubConnectionError("Network error while contacting GitHub.") from exc

        self._log_rate_limit(response)

        if response.status_code == 404:
            raise GitHubRepositoryNotFoundError(f"GitHub repository '{full_name}' not found.")
        if response.status_code == 401:
            raise GitHubAuthenticationError("GitHub rejected our credentials.")
        if response.status_code == 403 and self._is_rate_limited(response):
            raise GitHubRateLimitError("GitHub API rate limit exceeded.")
        if response.status_code >= 500:
            raise GitHubServerError("GitHub returned a server error.")
        if response.status_code >= 400:
            raise GitHubResponseError("GitHub rejected the request.")

        try:
            payload = response.json()
        except ValueError as exc:
            raise GitHubResponseError("GitHub returned a malformed response.") from exc

        try:
            return GitHubRepository.model_validate(payload)
        except ValidationError as exc:
            raise GitHubResponseError("GitHub response was missing expected fields.") from exc

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
