from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, get_github_oauth_client
from app.config import get_settings
from app.github.oauth_client import GitHubOAuthClient
from app.models.user import User
from app.schemas.user import UserResponse
from app.services.auth import (
    OAUTH_STATE_COOKIE_NAME,
    PKCE_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    MissingAuthorizationCodeError,
    MissingPkceVerifierError,
    complete_github_login,
    delete_session,
    derive_pkce_code_challenge,
    generate_oauth_state,
    generate_pkce_code_verifier,
    validate_oauth_state,
)

router = APIRouter(prefix="/auth")


def _cookie_secure() -> bool:
    return get_settings().app_env == "production"


@router.get("/github/login")
async def github_login(
    oauth_client: Annotated[GitHubOAuthClient, Depends(get_github_oauth_client)],
) -> RedirectResponse:
    settings = get_settings()
    state = generate_oauth_state()
    code_verifier = generate_pkce_code_verifier()
    code_challenge = derive_pkce_code_challenge(code_verifier)

    response = RedirectResponse(
        url=oauth_client.build_authorize_url(state, code_challenge),
        status_code=status.HTTP_302_FOUND,
    )
    response.set_cookie(
        OAUTH_STATE_COOKIE_NAME,
        state,
        max_age=settings.oauth_state_max_age_seconds,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
        path="/",
    )
    response.set_cookie(
        PKCE_COOKIE_NAME,
        code_verifier,
        max_age=settings.oauth_state_max_age_seconds,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
        path="/",
    )
    return response


@router.get("/github/callback")
async def github_callback(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    oauth_client: Annotated[GitHubOAuthClient, Depends(get_github_oauth_client)],
) -> Response:
    cookie_state = request.cookies.get(OAUTH_STATE_COOKIE_NAME)
    query_state = request.query_params.get("state")

    # State is validated first and unconditionally -- it is our CSRF guard for
    # the whole callback, including the "user denied" branch below.
    validate_oauth_state(cookie_state, query_state)

    if request.query_params.get("error") is not None:
        response: Response = JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"detail": "GitHub authorization was cancelled."},
        )
        response.delete_cookie(OAUTH_STATE_COOKIE_NAME, path="/")
        response.delete_cookie(PKCE_COOKIE_NAME, path="/")
        return response

    code = request.query_params.get("code")
    if not code:
        raise MissingAuthorizationCodeError

    code_verifier = request.cookies.get(PKCE_COOKIE_NAME)
    if not code_verifier:
        raise MissingPkceVerifierError

    settings = get_settings()
    _, raw_token, _ = await complete_github_login(code, code_verifier, db, oauth_client)

    # "/" (the frontend's own root), not "/auth/me" -- once signed in, a
    # user should land on the actual dashboard, which calls GET /auth/me
    # itself on mount, not on a raw JSON page. Correct whether or not a
    # frontend build is present: with no frontend/dist, "/" simply falls
    # through to a normal 404 (see app/frontend.py), no worse than before.
    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        raw_token,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
        path="/",
    )
    response.delete_cookie(OAUTH_STATE_COOKIE_NAME, path="/")
    response.delete_cookie(PKCE_COOKIE_NAME, path="/")
    return response


@router.get("/me", response_model=UserResponse)
async def read_current_user(user: Annotated[User, Depends(get_current_user)]) -> User:
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request, response: Response, db: Annotated[AsyncSession, Depends(get_db)]
) -> None:
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if raw_token:
        await delete_session(db, raw_token)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
