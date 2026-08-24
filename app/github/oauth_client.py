import logging
from urllib.parse import urlencode

import httpx
from pydantic import ValidationError

from app.github.errors import (
    GitHubAuthenticationError,
    GitHubConnectionError,
    GitHubOAuthError,
    GitHubResponseError,
    GitHubServerError,
    GitHubTimeoutError,
)
from app.github.schemas import GitHubAuthenticatedUser

logger = logging.getLogger(__name__)

_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_TOKEN_URL = "https://github.com/login/oauth/access_token"
_USER_URL = "https://api.github.com/user"


class GitHubOAuthClient:
    """GitHub-specific OAuth authorization-code flow operations.

    Separate from GitHubClient: this talks to github.com's OAuth authorization
    server (a different host and request/response shape) rather than the
    api.github.com REST API used for repository lookups.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        client_id: str,
        client_secret: str,
        callback_url: str,
    ) -> None:
        self._http_client = http_client
        self._client_id = client_id
        self._client_secret = client_secret
        self._callback_url = callback_url

    def build_authorize_url(self, state: str, code_challenge: str) -> str:
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._callback_url,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{_AUTHORIZE_URL}?{urlencode(params)}"

    async def exchange_code_for_token(self, code: str, code_verifier: str) -> str:
        try:
            response = await self._http_client.post(
                _TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "code": code,
                    "redirect_uri": self._callback_url,
                    "code_verifier": code_verifier,
                },
            )
        except httpx.TimeoutException as exc:
            raise GitHubTimeoutError("Timed out contacting GitHub.") from exc
        except httpx.ConnectError as exc:
            raise GitHubConnectionError("Could not connect to GitHub.") from exc
        except httpx.HTTPError as exc:
            raise GitHubConnectionError("Network error while contacting GitHub.") from exc

        if response.status_code >= 500:
            raise GitHubServerError("GitHub returned a server error during token exchange.")
        if response.status_code >= 400:
            raise GitHubOAuthError("GitHub rejected the OAuth token exchange request.")

        try:
            payload = response.json()
        except ValueError as exc:
            raise GitHubResponseError("GitHub returned a malformed token response.") from exc

        if "error" in payload:
            raise GitHubOAuthError("GitHub OAuth token exchange failed.")

        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise GitHubResponseError("GitHub token response was missing an access token.")

        return access_token

    async def get_authenticated_user(self, access_token: str) -> GitHubAuthenticatedUser:
        try:
            response = await self._http_client.get(
                _USER_URL,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {access_token}",
                },
            )
        except httpx.TimeoutException as exc:
            raise GitHubTimeoutError("Timed out contacting GitHub.") from exc
        except httpx.ConnectError as exc:
            raise GitHubConnectionError("Could not connect to GitHub.") from exc
        except httpx.HTTPError as exc:
            raise GitHubConnectionError("Network error while contacting GitHub.") from exc

        if response.status_code == 401:
            raise GitHubAuthenticationError("GitHub rejected our credentials.")
        if response.status_code >= 500:
            raise GitHubServerError("GitHub returned a server error.")
        if response.status_code >= 400:
            raise GitHubResponseError("GitHub rejected the request.")

        try:
            payload = response.json()
        except ValueError as exc:
            raise GitHubResponseError("GitHub returned a malformed response.") from exc

        try:
            return GitHubAuthenticatedUser.model_validate(payload)
        except ValidationError as exc:
            raise GitHubResponseError("GitHub user response was missing expected fields.") from exc
