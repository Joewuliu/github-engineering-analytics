import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.github.errors import (
    GitHubAuthenticationError,
    GitHubConnectionError,
    GitHubRateLimitError,
    GitHubRepositoryNotFoundError,
    GitHubResponseError,
    GitHubServerError,
    GitHubTimeoutError,
)
from app.services.repository_import import RepositoryAlreadyExistsError

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
    RepositoryAlreadyExistsError: (409, "This repository is already tracked."),
}


def register_exception_handlers(app: FastAPI) -> None:
    for exc_type, (status_code, message) in _ERROR_MAPPING.items():
        app.add_exception_handler(exc_type, _make_handler(status_code, message))


def _make_handler(status_code: int, message: str) -> ExceptionHandler:
    async def handler(request: Request, exc: Exception) -> JSONResponse:
        logger.warning("%s mapped to HTTP %s: %s", type(exc).__name__, status_code, exc)
        return JSONResponse(status_code=status_code, content={"detail": message})

    return handler
