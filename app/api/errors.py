import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.github.errors import (
    GitHubAuthenticationError,
    GitHubConnectionError,
    GitHubOAuthError,
    GitHubRateLimitError,
    GitHubRepositoryNotFoundError,
    GitHubResponseError,
    GitHubServerError,
    GitHubTimeoutError,
)
from app.services.auth import (
    OAUTH_STATE_COOKIE_NAME,
    PKCE_COOKIE_NAME,
    GitHubOAuthNotConfiguredError,
    InvalidOAuthStateError,
    MissingAuthorizationCodeError,
    MissingPkceVerifierError,
    UnauthenticatedError,
)
from app.services.repositories import RepositoryAlreadyTrackedError

logger = logging.getLogger(__name__)

ExceptionHandler = Callable[[Request, Exception], Awaitable[JSONResponse]]

_ERROR_MAPPING: dict[type[Exception], tuple[int, str]] = {
    GitHubRepositoryNotFoundError: (404, "GitHub repository not found."),
    GitHubAuthenticationError: (502, "Upstream GitHub authentication failed."),
    GitHubRateLimitError: (503, "GitHub API rate limit exceeded. Try again later."),
    GitHubServerError: (502, "GitHub is currently unavailable."),
    GitHubConnectionError: (502, "Could not reach GitHub."),
    GitHubTimeoutError: (504, "Timed out waiting for GitHub."),
    GitHubResponseError: (502, "GitHub returned an unexpected response."),
    GitHubOAuthError: (502, "GitHub OAuth token exchange failed."),
    RepositoryAlreadyTrackedError: (409, "You already track this repository."),
    GitHubOAuthNotConfiguredError: (500, "GitHub OAuth is not configured."),
    UnauthenticatedError: (401, "Not authenticated."),
}

# These are the pre-token-exchange callback validation failures: state
# mismatch, missing code, missing PKCE verifier. They get a dedicated handler
# that also clears the (now-useless, single-use) oauth_state/pkce_verifier
# cookies, rather than the generic handler used for everything else.
_OAUTH_CALLBACK_VALIDATION_ERRORS: dict[type[Exception], tuple[int, str]] = {
    InvalidOAuthStateError: (400, "Missing or invalid OAuth state."),
    MissingAuthorizationCodeError: (400, "Missing authorization code."),
    MissingPkceVerifierError: (400, "Missing PKCE verifier."),
}


def register_exception_handlers(app: FastAPI) -> None:
    for exc_type, (status_code, message) in _ERROR_MAPPING.items():
        app.add_exception_handler(exc_type, _make_handler(status_code, message))
    for exc_type, (status_code, message) in _OAUTH_CALLBACK_VALIDATION_ERRORS.items():
        app.add_exception_handler(exc_type, _make_oauth_callback_handler(status_code, message))


def _make_handler(status_code: int, message: str) -> ExceptionHandler:
    async def handler(request: Request, exc: Exception) -> JSONResponse:
        logger.warning("%s mapped to HTTP %s: %s", type(exc).__name__, status_code, exc)
        return JSONResponse(status_code=status_code, content={"detail": message})

    return handler


def _make_oauth_callback_handler(status_code: int, message: str) -> ExceptionHandler:
    async def handler(request: Request, exc: Exception) -> JSONResponse:
        logger.warning("%s mapped to HTTP %s: %s", type(exc).__name__, status_code, exc)
        response = JSONResponse(status_code=status_code, content={"detail": message})
        response.delete_cookie(OAUTH_STATE_COOKIE_NAME, path="/")
        response.delete_cookie(PKCE_COOKIE_NAME, path="/")
        return response

    return handler
